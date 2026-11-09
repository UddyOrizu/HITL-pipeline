import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError } from "../api";
import { activateOnKey, DocumentView, type CommentT, type FlagT, type SectionT } from "../components/DocumentView";
import { References, type RefSummary } from "../components/review/References";
import { Badge, ErrorBox, label, pct, riskTone } from "../components/ui";
import { TelemetryClient } from "../telemetry";
import "./review.css";

type View = {
  task: { id: string; kind: string; stage: string; status: string; due_at: string | null; effort_minutes: number };
  submission: {
    id: string; title: string; risk_tier: string; content_origin: string; ai_model: string | null; workflow: string;
    external_ref: string | null; reference_documents: (RefSummary | string)[]; previous_submission_id?: string | null;
  };
  document: { id: string; filename: string; render_kind: string; sections: SectionT[] };
  flags: FlagT[];
  comments: CommentT[];
  prior_stages: { stage: string; outcome: string; reason: string }[];
  policy: { min_reading_ratio: number; min_coverage: number; action: string };
  telemetry: Record<string, number>;
};

type Reveal = { type: string; difficulty: string; caught: boolean; canary_text: string; original_text: string | null; explanation: string; message: string };
type Outcome = "approve" | "reject" | "request_changes";
type Mode = "comment" | "edit";

// --- In-progress work kept in sessionStorage (per tab), so a reload or a sign-in after the session expires
// brings the reviewer back to what they were typing. Cleared once the decision is recorded.

type Draft = { commentText: string; editText: string; mode: Mode; outcome: Outcome; reason: string; selected: string | null; cited: string[] };

const OUTCOMES: Outcome[] = ["approve", "request_changes", "reject"];
const draftKey = (taskId: string) => `por.reviewDraft.${taskId}`;

function readDraft(taskId: string | undefined): Draft | null {
  if (!taskId) return null;
  try {
    const raw = sessionStorage.getItem(draftKey(taskId));
    if (!raw) return null;
    const d = JSON.parse(raw);
    return {
      commentText: typeof d.commentText === "string" ? d.commentText : "",
      editText: typeof d.editText === "string" ? d.editText : "",
      mode: d.mode === "edit" ? "edit" : "comment",
      outcome: OUTCOMES.includes(d.outcome) ? d.outcome : "approve",
      reason: typeof d.reason === "string" ? d.reason : "",
      selected: typeof d.selected === "string" ? d.selected : null,
      cited: Array.isArray(d.cited) ? d.cited.filter((x: unknown) => typeof x === "string") : [],
    };
  } catch {
    return null;
  }
}

function hasContent(d: Draft | null) {
  return !!d && !!(d.commentText.trim() || d.editText.trim() || d.reason.trim() || d.outcome !== "approve" || d.cited.length);
}

function writeDraft(taskId: string, d: Draft) {
  try {
    if (hasContent(d)) sessionStorage.setItem(draftKey(taskId), JSON.stringify(d));
    else sessionStorage.removeItem(draftKey(taskId));
  } catch {
    /* storage unavailable or full: drafts are a convenience */
  }
}

function clearDraft(taskId: string | undefined) {
  if (!taskId) return;
  try {
    sessionStorage.removeItem(draftKey(taskId));
  } catch {
    /* ignore */
  }
}

function refList(v: View): RefSummary[] {
  return (v.submission.reference_documents || []).map((d) => (typeof d === "string" ? { id: d, filename: d, render_kind: "" } : d));
}

export default function Workspace() {
  const { taskId } = useParams();
  const nav = useNavigate();
  const [initial] = useState(() => readDraft(taskId));
  const [view, setView] = useState<View | null>(null);
  const [loadError, setLoadError] = useState<unknown>(null); // the task could not be loaded at all
  const [refreshError, setRefreshError] = useState<unknown>(null); // a later reload failed; the page stays usable
  const [error, setError] = useState<unknown>(null); // decision errors
  const [flagError, setFlagError] = useState<unknown>(null);
  const [commentError, setCommentError] = useState<unknown>(null);
  const [pending, setPending] = useState<string | null>(null); // key of the flag/comment action in flight
  const [selected, setSelected] = useState<string | null>(initial?.selected ?? null);
  const [cited, setCited] = useState<Set<string>>(() => new Set(initial?.cited ?? []));
  const [commentText, setCommentText] = useState(initial?.commentText ?? "");
  const [editText, setEditText] = useState(initial?.editText ?? "");
  const [mode, setMode] = useState<Mode>(initial?.mode ?? "comment");
  const [outcome, setOutcome] = useState<Outcome>(initial?.outcome ?? "approve");
  const [reason, setReason] = useState(initial?.reason ?? "");
  const [restored, setRestored] = useState(hasContent(initial));
  const [warnings, setWarnings] = useState<string[] | null>(null);
  const [reveal, setReveal] = useState<{ items: Reveal[]; recorded: boolean; message?: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState<"review" | "refs">("review");
  const [refsOpened, setRefsOpened] = useState(false);
  const telemetry = useRef<TelemetryClient | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const hasView = useRef(false);
  const draftFor = useRef<string | undefined>(taskId);
  const decided = useRef(false);

  async function load() {
    try {
      const v = await api<View>(`/v1/review/tasks/${taskId}`);
      hasView.current = true;
      setView(v);
      setRefreshError(null);
    } catch (e) {
      if (hasView.current) setRefreshError(e);
      else setLoadError(e);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId]);

  // Another task opened in the same component: restore that task's draft (or start clean).
  useEffect(() => {
    if (draftFor.current === taskId) return;
    draftFor.current = taskId;
    decided.current = false;
    hasView.current = false;
    setView(null);
    setLoadError(null);
    setRefreshError(null);
    setReveal(null);
    const d = readDraft(taskId);
    setSelected(d?.selected ?? null);
    setCited(new Set(d?.cited ?? []));
    setCommentText(d?.commentText ?? "");
    setEditText(d?.editText ?? "");
    setMode(d?.mode ?? "comment");
    setOutcome(d?.outcome ?? "approve");
    setReason(d?.reason ?? "");
    setRestored(hasContent(d));
    setWarnings(null);
    setError(null);
    setFlagError(null);
    setCommentError(null);
    setTab("review");
    setRefsOpened(false);
  }, [taskId]);

  // Save the draft as it changes.
  useEffect(() => {
    if (!taskId || draftFor.current !== taskId || decided.current) return;
    writeDraft(taskId, { commentText, editText, mode, outcome, reason, selected, cited: [...cited] });
  }, [taskId, commentText, editText, mode, outcome, reason, selected, cited]);

  useEffect(() => {
    if (!view || telemetry.current) return;
    const t = new TelemetryClient(view.task.id);
    telemetry.current = t;
    requestAnimationFrame(() => t.start(scrollRef.current));
    return () => {
      t.stop();
      telemetry.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view?.task.id]);

  useEffect(() => {
    telemetry.current?.observeSections();
  }, [view]);

  const spanText = useMemo(() => {
    const m = new Map<string, string>();
    view?.document.sections.forEach((s) => s.spans.forEach((sp) => m.set(sp.id, sp.text)));
    return m;
  }, [view]);

  // A restored draft may point at passages that no longer exist (e.g. a different document version).
  useEffect(() => {
    if (!view) return;
    if (selected && !spanText.has(selected)) setSelected(null);
    if ([...cited].some((id) => !spanText.has(id))) setCited(new Set([...cited].filter((id) => spanText.has(id))));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spanText]);

  if (loadError) return <ErrorBox error={loadError} />;
  if (!view) return <div className="empty">Loading…</div>;

  const refs = refList(view);
  const flagsFor = (spanId: string | null) => view.flags.filter((f) => f.span_id === spanId);
  const pendingMandatory = view.flags.filter((f) => f.mandatory && !f.acknowledged);
  const selectedFlags = flagsFor(selected);

  async function openFlag(f: FlagT) {
    setSelected(f.span_id);
    document.querySelector(`[data-span-id="${f.span_id}"]`)?.scrollIntoView({ behavior: "smooth", block: "center" });
    if (f.opened || pending === `open:${f.id}`) return;
    setFlagError(null);
    setPending(`open:${f.id}`);
    try {
      await api(`/v1/review/tasks/${taskId}/flags/${f.id}/open`, { method: "POST" });
      telemetry.current?.push({ type: "flag_opened", span_id: f.span_id });
      await load();
    } catch (e) {
      setFlagError(e);
    } finally {
      setPending(null);
    }
  }

  async function ack(f: FlagT) {
    setFlagError(null);
    setPending(`ack:${f.id}`);
    try {
      await api(`/v1/review/tasks/${taskId}/flags/${f.id}/acknowledge`, { method: "POST" });
      await load();
    } catch (e) {
      setFlagError(e);
    } finally {
      setPending(null);
    }
  }

  async function addComment() {
    if (!selected) return;
    const body = mode === "edit"
      ? { span_id: selected, kind: "edit", body: commentText, suggested_text: editText }
      : { span_id: selected, kind: "comment", body: commentText };
    setCommentError(null);
    setPending("comment");
    try {
      await api(`/v1/review/tasks/${taskId}/comments`, { body });
      telemetry.current?.push({ type: mode === "edit" ? "edit_made" : "comment_added", span_id: selected });
      setCommentText("");
      setEditText("");
      await load();
    } catch (e) {
      setCommentError(e); // the text stays in the box (and in the draft) so nothing is lost
    } finally {
      setPending(null);
    }
  }

  async function removeComment(id: string) {
    setCommentError(null);
    setPending(`remove:${id}`);
    try {
      await api(`/v1/review/tasks/${taskId}/comments/${id}`, { method: "DELETE" });
      await load();
    } catch (e) {
      setCommentError(e);
    } finally {
      setPending(null);
    }
  }

  function toggleCite(id: string) {
    const next = new Set(cited);
    next.has(id) ? next.delete(id) : next.add(id);
    setCited(next);
  }

  function discardDraft() {
    setCommentText("");
    setEditText("");
    setMode("comment");
    setOutcome("approve");
    setReason("");
    setCited(new Set());
    setRestored(false);
    clearDraft(taskId);
  }

  async function submit(confirm = false) {
    setBusy(true);
    setError(null);
    try {
      telemetry.current?.push({ type: "decision_submitted" });
      await telemetry.current?.flush();
      const r = await api<any>(`/v1/review/tasks/${taskId}/decision`, {
        body: { outcome, reason, cited_span_ids: [...cited], confirm_warnings: confirm },
      });
      setWarnings(null);
      if (r.status === "canary_caught") {
        // Not recorded: the reviewer carries on with this task. The draft keeps any unsent comment text.
        setReveal({ items: r.canary, recorded: false, message: r.message });
        setCited(new Set());
        setOutcome("approve");
        setReason("");
        load();
      } else {
        decided.current = true;
        clearDraft(taskId);
        setRestored(false);
        if (r.canary?.length) setReveal({ items: r.canary, recorded: true });
        else nav(view!.task.kind === "spot_check" ? "/spot-checks" : "/queue");
      }
    } catch (e) {
      if (e instanceof ApiError && e.detail?.code === "confirm_required") setWarnings(e.detail.reasons);
      else if (e instanceof ApiError && e.detail?.code === "below_review_standard") setError(new Error(`${e.detail.message}: ${e.detail.reasons.join(" ")}`));
      else setError(e);
    } finally {
      setBusy(false);
    }
  }

  function selectTab(next: "review" | "refs") {
    setTab(next);
    if (next === "refs") setRefsOpened(true);
  }

  function onTabKey(e: React.KeyboardEvent) {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const next = tab === "review" ? "refs" : "review";
    selectTab(next);
    document.getElementById(`ws-tab-${next}`)?.focus();
  }

  const t = view.telemetry;
  const canApprove = pendingMandatory.length === 0;
  const backTo = view.task.kind === "spot_check" ? "/spot-checks" : "/queue";

  return (
    <>
      <div className="page-head">
        <div>
          <div className="sub"><Link to={backTo}>← Back to queue</Link></div>
          <h1>{view.submission.title}</h1>
          <div className="btn-row" style={{ alignItems: "center" }}>
            <Badge tone={riskTone(view.submission.risk_tier)}>{view.submission.risk_tier} risk</Badge>
            {view.submission.previous_submission_id && (
              <Badge tone="accent">revision of {view.submission.previous_submission_id.slice(0, 8)}</Badge>
            )}
            <Badge>{label(view.submission.content_origin)}</Badge>
            {view.task.kind === "spot_check" && <Badge tone="medium">blind spot-check</Badge>}
            {refs.length > 0 && <Badge>{refs.length} reference document{refs.length === 1 ? "" : "s"}</Badge>}
            <span className="sub">{view.submission.workflow} · {view.task.stage} · about {view.task.effort_minutes} min</span>
          </div>
        </div>
      </div>

      {refreshError != null && (
        <div className="notice" role="status">
          Couldn't refresh this task: {refreshError instanceof Error ? refreshError.message : String(refreshError)}{" "}
          <button className="linklike" onClick={() => load()}>Try again</button>
        </div>
      )}

      <div className="workspace">
        <div className="doc-scroll" ref={scrollRef}>
          <DocumentView renderKind={view.document.render_kind} sections={view.document.sections} flags={view.flags}
            comments={view.comments} cited={cited} selected={selected} onSelect={setSelected} />
        </div>

        <div className="panel">
          {refs.length > 0 && (
            <div className="tabs" role="tablist" aria-label="Side panel" onKeyDown={onTabKey}>
              <button id="ws-tab-review" role="tab" aria-selected={tab === "review"} aria-controls="ws-panel-review"
                tabIndex={tab === "review" ? 0 : -1} onClick={() => selectTab("review")}>Review</button>
              <button id="ws-tab-refs" role="tab" aria-selected={tab === "refs"} aria-controls="ws-panel-refs"
                tabIndex={tab === "refs" ? 0 : -1} onClick={() => selectTab("refs")}>References ({refs.length})</button>
            </div>
          )}

          {refsOpened && (
            <div id="ws-panel-refs" role="tabpanel" aria-labelledby="ws-tab-refs" style={{ display: tab === "refs" ? "block" : "none" }}>
              <References taskId={view.task.id} expected={refs} />
            </div>
          )}

          <div id="ws-panel-review" role={refs.length > 0 ? "tabpanel" : undefined} aria-labelledby={refs.length > 0 ? "ws-tab-review" : undefined}
            style={{ display: tab === "review" ? "flex" : "none", flexDirection: "column", gap: 14 }}>
            {view.prior_stages.length > 0 && (
              <div className="card">
                <h3>Earlier stages</h3>
                {view.prior_stages.map((p, i) => (
                  <div key={i} className="comment"><strong>{p.stage}</strong>: {label(p.outcome)}{p.reason ? ` — ${p.reason}` : ""}</div>
                ))}
              </div>
            )}

            <div className="card">
              <h3>Where to look ({view.flags.length})</h3>
              {pendingMandatory.length > 0 && (
                <div className="notice">{pendingMandatory.length} high-risk flag(s) must be opened and acknowledged before you can approve.</div>
              )}
              <ErrorBox error={flagError} />
              {view.flags.length === 0 && <p className="sub">No risk flags were raised. Read the whole document.</p>}
              {view.flags.map((f) => {
                const state = f.acknowledged ? "acknowledged" : f.opened ? "opened, not yet acknowledged" : "not opened";
                return (
                  // The whole card is clickable with a mouse; the inner role="button" is the keyboard/screen-reader target
                  // (kept separate so the Acknowledge button isn't nested inside another button).
                  <div key={f.id} className={`flag-item ${f.acknowledged ? "done" : ""} ${selected === f.span_id ? "active" : ""}`}
                    onClick={() => openFlag(f)}>
                    <div className="flag-open" role="button" tabIndex={0} aria-pressed={selected === f.span_id}
                      aria-label={`${f.severity} ${label(f.category)} flag${f.mandatory ? ", required" : ""}, ${state}: ${f.rationale}. Show the passage.`}
                      onKeyDown={activateOnKey(() => openFlag(f))}>
                      <div className="btn-row" style={{ alignItems: "center" }}>
                        <Badge tone={f.severity}>{f.severity}</Badge>
                        <strong style={{ fontSize: 13 }}>{label(f.category)}</strong>
                        {f.mandatory && <Badge tone="high">required</Badge>}
                        {pending === `open:${f.id}` && <span className="sub" style={{ fontSize: 12 }}>Opening…</span>}
                      </div>
                      <div className="rationale">{f.rationale}</div>
                    </div>
                    {f.opened && !f.acknowledged && (
                      <button className="btn small" disabled={pending === `ack:${f.id}`}
                        aria-label={`Acknowledge ${label(f.category)} flag`}
                        onClick={(e) => (e.stopPropagation(), ack(f))}>
                        {pending === `ack:${f.id}` ? "Acknowledging…" : "Acknowledge"}
                      </button>
                    )}
                    {f.acknowledged && <span className="sub" style={{ fontSize: 12 }}>Acknowledged</span>}
                  </div>
                );
              })}
            </div>

            <div className="card">
              <h3>Selected passage</h3>
              {!selected ? (
                <p className="sub">Click any passage (or Tab to it and press Enter) to comment on it, suggest an edit, or cite it in your decision.</p>
              ) : (
                <>
                  <p style={{ fontSize: 13, fontFamily: "var(--serif)" }}>{spanText.get(selected)}</p>
                  {selectedFlags.map((f) => <div key={f.id} className="sub" style={{ fontSize: 12 }}>⚑ {f.rationale}</div>)}
                  <div className="btn-row" style={{ margin: "8px 0" }}>
                    <button className="btn small" aria-pressed={cited.has(selected)} onClick={() => toggleCite(selected)}>{cited.has(selected) ? "Uncite" : "Cite in decision"}</button>
                    <div className="segmented" role="group" aria-label="Comment type">
                      <button className={mode === "comment" ? "on" : ""} aria-pressed={mode === "comment"} onClick={() => setMode("comment")}>Comment</button>
                      <button className={mode === "edit" ? "on" : ""} aria-pressed={mode === "edit"}
                        onClick={() => { if (mode !== "edit" || !editText) setEditText(spanText.get(selected) || ""); setMode("edit"); }}>Suggest edit</button>
                    </div>
                  </div>
                  <textarea aria-label={mode === "edit" ? "Why this change?" : "Your comment"} placeholder={mode === "edit" ? "Why this change?" : "Your comment"}
                    value={commentText} onChange={(e) => setCommentText(e.target.value)} />
                  {mode === "edit" && <textarea aria-label="Suggested text" placeholder="Suggested text" value={editText} onChange={(e) => setEditText(e.target.value)} style={{ marginTop: 6 }} />}
                  <ErrorBox error={commentError} />
                  <button className="btn small primary" style={{ marginTop: 6 }}
                    disabled={pending === "comment" || (mode === "comment" ? !commentText : !editText)} onClick={addComment}>
                    {pending === "comment" ? "Adding…" : "Add"}
                  </button>
                </>
              )}
              {!selected && <ErrorBox error={commentError} />}
              {view.comments.length > 0 && <h3 style={{ marginTop: 14 }}>Your comments</h3>}
              {view.comments.map((c) => (
                <div key={c.id} className="comment">
                  {c.kind === "edit" && <Badge tone="accent">edit</Badge>} {c.body}
                  {c.suggested_text && <div className="sub">→ {c.suggested_text}</div>}
                  <div>
                    <button className="linklike" disabled={pending === `remove:${c.id}`} onClick={() => removeComment(c.id)}
                      aria-label={`Remove comment: ${c.body || c.suggested_text || ""}`.slice(0, 120)}>
                      {pending === `remove:${c.id}` ? "Removing…" : "Remove"}
                    </button>
                  </div>
                </div>
              ))}
            </div>

            <div className="card">
              <h3>Decision</h3>
              {restored && (
                <p className="sub draft-note" role="status">
                  Restored your unsent draft from earlier in this session.{" "}
                  <button className="linklike" onClick={discardDraft}>Discard draft</button>
                </p>
              )}
              <div className="segmented" role="group" aria-label="Outcome" style={{ marginBottom: 8 }}>
                <button className={outcome === "approve" ? "on" : ""} aria-pressed={outcome === "approve"} onClick={() => setOutcome("approve")}>Approve</button>
                <button className={outcome === "request_changes" ? "on" : ""} aria-pressed={outcome === "request_changes"} onClick={() => setOutcome("request_changes")}>Request changes</button>
                <button className={outcome === "reject" ? "on" : ""} aria-pressed={outcome === "reject"} onClick={() => setOutcome("reject")}>Reject</button>
              </div>
              <textarea aria-label={outcome === "approve" ? "Notes (optional)" : "Reason (required)"} placeholder={outcome === "approve" ? "Notes (optional)" : "Reason (required)"}
                value={reason} onChange={(e) => setReason(e.target.value)} />
              {cited.size > 0 && <p className="sub" style={{ fontSize: 12 }}>Citing {cited.size} passage(s).</p>}
              {warnings && (
                <div className="notice">
                  <strong>This approval is below the minimum review standard.</strong>
                  <ul style={{ margin: "6px 0", paddingLeft: 18 }}>{warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
                  <button className="btn small" onClick={() => submit(true)} disabled={busy}>Approve anyway (recorded)</button>
                </div>
              )}
              <ErrorBox error={error} />
              <button className="btn primary" style={{ marginTop: 8, width: "100%", justifyContent: "center" }}
                disabled={busy || (outcome === "approve" && !canApprove) || (outcome !== "approve" && !reason.trim())}
                onClick={() => submit(false)}>
                {busy ? "Submitting…" : "Submit decision"}
              </button>
            </div>

            <div className="card">
              <h3>Your review so far</h3>
              <div className="kv">
                <span className="k">Active reading</span><span>{Math.round(t.active_seconds)}s of ~{Math.round(t.expected_seconds)}s</span>
                <span className="k">Sections viewed</span><span>{t.sections_viewed} / {t.sections_total}</span>
                <span className="k">Flags acknowledged</span><span>{t.flags_acknowledged} / {t.flags_total}</span>
              </div>
              <div className="meter" style={{ marginTop: 8 }}><div style={{ width: pct(Math.min(1, t.reading_ratio)) }} /></div>
              <p className="sub" style={{ fontSize: 11, marginTop: 8 }}>
                Only reading inside this page is measured. No webcam, keystroke content or activity elsewhere.
              </p>
            </div>
          </div>
        </div>
      </div>

      {reveal && (
        <div className="modal-back">
          <div className="modal" role="dialog" aria-modal="true" aria-label="Canary result">
            {reveal.items.map((c, i) => (
              <div key={i}>
                <h2>{c.caught ? "You caught a canary" : "This review contained a canary"}</h2>
                <p>{c.message}</p>
                <p className="sub">{c.explanation} Difficulty: {c.difficulty}.</p>
                <div className="canary-text">{c.canary_text}</div>
                {c.original_text && <><div className="sub">Original:</div><div className="canary-text">{c.original_text}</div></>}
              </div>
            ))}
            {!reveal.recorded && <div className="success">{reveal.message}</div>}
            <div className="btn-row" style={{ marginTop: 12 }}>
              <button className="btn primary" autoFocus onClick={() => {
                const recorded = reveal.recorded;
                setReveal(null);
                if (recorded) nav(backTo);
              }}>
                {reveal.recorded ? "Back to queue" : "Continue reviewing"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
