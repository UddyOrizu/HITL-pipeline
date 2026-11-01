import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError } from "../api";
import { DocumentView, type CommentT, type FlagT, type SectionT } from "../components/DocumentView";
import { Badge, ErrorBox, label, pct, riskTone } from "../components/ui";
import { TelemetryClient } from "../telemetry";

type View = {
  task: { id: string; kind: string; stage: string; status: string; due_at: string | null; effort_minutes: number };
  submission: { id: string; title: string; risk_tier: string; content_origin: string; ai_model: string | null; workflow: string; external_ref: string | null; reference_documents: string[] };
  document: { id: string; filename: string; render_kind: string; sections: SectionT[] };
  flags: FlagT[];
  comments: CommentT[];
  prior_stages: { stage: string; outcome: string; reason: string }[];
  policy: { min_reading_ratio: number; min_coverage: number; action: string };
  telemetry: Record<string, number>;
};

type Reveal = { type: string; difficulty: string; caught: boolean; canary_text: string; original_text: string | null; explanation: string; message: string };

export default function Workspace() {
  const { taskId } = useParams();
  const nav = useNavigate();
  const [view, setView] = useState<View | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [cited, setCited] = useState<Set<string>>(new Set());
  const [commentText, setCommentText] = useState("");
  const [editText, setEditText] = useState("");
  const [mode, setMode] = useState<"comment" | "edit">("comment");
  const [outcome, setOutcome] = useState<"approve" | "reject" | "request_changes">("approve");
  const [reason, setReason] = useState("");
  const [warnings, setWarnings] = useState<string[] | null>(null);
  const [reveal, setReveal] = useState<{ items: Reveal[]; recorded: boolean; message?: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const telemetry = useRef<TelemetryClient | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  async function load() {
    try {
      setView(await api<View>(`/v1/review/tasks/${taskId}`));
    } catch (e) {
      setError(e);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId]);

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

  if (error) return <ErrorBox error={error} />;
  if (!view) return <div className="empty">Loading…</div>;

  const flagsFor = (spanId: string | null) => view.flags.filter((f) => f.span_id === spanId);
  const pendingMandatory = view.flags.filter((f) => f.mandatory && !f.acknowledged);
  const selectedFlags = flagsFor(selected);

  async function openFlag(f: FlagT) {
    setSelected(f.span_id);
    document.querySelector(`[data-span-id="${f.span_id}"]`)?.scrollIntoView({ behavior: "smooth", block: "center" });
    if (!f.opened) {
      telemetry.current?.push({ type: "flag_opened", span_id: f.span_id });
      await api(`/v1/review/tasks/${taskId}/flags/${f.id}/open`, { method: "POST" });
      load();
    }
  }

  async function ack(f: FlagT) {
    await api(`/v1/review/tasks/${taskId}/flags/${f.id}/acknowledge`, { method: "POST" });
    load();
  }

  async function addComment() {
    if (!selected) return;
    const body = mode === "edit"
      ? { span_id: selected, kind: "edit", body: commentText, suggested_text: editText }
      : { span_id: selected, kind: "comment", body: commentText };
    await api(`/v1/review/tasks/${taskId}/comments`, { body });
    telemetry.current?.push({ type: mode === "edit" ? "edit_made" : "comment_added", span_id: selected });
    setCommentText("");
    setEditText("");
    load();
  }

  async function removeComment(id: string) {
    await api(`/v1/review/tasks/${taskId}/comments/${id}`, { method: "DELETE" });
    load();
  }

  function toggleCite(id: string) {
    const next = new Set(cited);
    next.has(id) ? next.delete(id) : next.add(id);
    setCited(next);
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
        setReveal({ items: r.canary, recorded: false, message: r.message });
        setCited(new Set());
        setOutcome("approve");
        setReason("");
        load();
      } else if (r.canary?.length) {
        setReveal({ items: r.canary, recorded: true });
      } else {
        nav(view!.task.kind === "spot_check" ? "/spot-checks" : "/queue");
      }
    } catch (e) {
      if (e instanceof ApiError && e.detail?.code === "confirm_required") setWarnings(e.detail.reasons);
      else if (e instanceof ApiError && e.detail?.code === "below_review_standard") setError(new Error(`${e.detail.message}: ${e.detail.reasons.join(" ")}`));
      else setError(e);
    } finally {
      setBusy(false);
    }
  }

  const t = view.telemetry;
  const canApprove = pendingMandatory.length === 0;

  return (
    <>
      <div className="page-head">
        <div>
          <div className="sub"><Link to={view.task.kind === "spot_check" ? "/spot-checks" : "/queue"}>← Back to queue</Link></div>
          <h1>{view.submission.title}</h1>
          <div className="btn-row" style={{ alignItems: "center" }}>
            <Badge tone={riskTone(view.submission.risk_tier)}>{view.submission.risk_tier} risk</Badge>
            <Badge>{label(view.submission.content_origin)}</Badge>
            {view.task.kind === "spot_check" && <Badge tone="medium">blind spot-check</Badge>}
            <span className="sub">{view.submission.workflow} · {view.task.stage} · about {view.task.effort_minutes} min</span>
          </div>
        </div>
      </div>

      <div className="workspace">
        <div className="doc-scroll" ref={scrollRef}>
          <DocumentView renderKind={view.document.render_kind} sections={view.document.sections} flags={view.flags}
            comments={view.comments} cited={cited} selected={selected} onSelect={setSelected} />
        </div>

        <div className="panel">
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
            {view.flags.length === 0 && <p className="sub">No risk flags were raised. Read the whole document.</p>}
            {view.flags.map((f) => (
              <div key={f.id} className={`flag-item ${f.acknowledged ? "done" : ""}`} onClick={() => openFlag(f)}>
                <div className="btn-row" style={{ alignItems: "center" }}>
                  <Badge tone={f.severity}>{f.severity}</Badge>
                  <strong style={{ fontSize: 13 }}>{label(f.category)}</strong>
                  {f.mandatory && <Badge tone="high">required</Badge>}
                </div>
                <div className="rationale">{f.rationale}</div>
                {f.opened && !f.acknowledged && (
                  <button className="btn small" onClick={(e) => (e.stopPropagation(), ack(f))}>Acknowledge</button>
                )}
                {f.acknowledged && <span className="sub" style={{ fontSize: 12 }}>Acknowledged</span>}
              </div>
            ))}
          </div>

          <div className="card">
            <h3>Selected passage</h3>
            {!selected ? (
              <p className="sub">Click any passage to comment on it, suggest an edit, or cite it in your decision.</p>
            ) : (
              <>
                <p style={{ fontSize: 13, fontFamily: "var(--serif)" }}>{spanText.get(selected)}</p>
                {selectedFlags.map((f) => <div key={f.id} className="sub" style={{ fontSize: 12 }}>⚑ {f.rationale}</div>)}
                <div className="btn-row" style={{ margin: "8px 0" }}>
                  <button className="btn small" onClick={() => toggleCite(selected)}>{cited.has(selected) ? "Uncite" : "Cite in decision"}</button>
                  <div className="segmented">
                    <button className={mode === "comment" ? "on" : ""} onClick={() => setMode("comment")}>Comment</button>
                    <button className={mode === "edit" ? "on" : ""} onClick={() => { setMode("edit"); setEditText(spanText.get(selected) || ""); }}>Suggest edit</button>
                  </div>
                </div>
                <textarea placeholder={mode === "edit" ? "Why this change?" : "Your comment"} value={commentText} onChange={(e) => setCommentText(e.target.value)} />
                {mode === "edit" && <textarea placeholder="Suggested text" value={editText} onChange={(e) => setEditText(e.target.value)} style={{ marginTop: 6 }} />}
                <button className="btn small primary" style={{ marginTop: 6 }} disabled={mode === "comment" ? !commentText : !editText} onClick={addComment}>Add</button>
              </>
            )}
            {view.comments.length > 0 && <h3 style={{ marginTop: 14 }}>Your comments</h3>}
            {view.comments.map((c) => (
              <div key={c.id} className="comment">
                {c.kind === "edit" && <Badge tone="accent">edit</Badge>} {c.body}
                {c.suggested_text && <div className="sub">→ {c.suggested_text}</div>}
                <div><a href="#" onClick={(e) => (e.preventDefault(), removeComment(c.id))} style={{ fontSize: 12 }}>Remove</a></div>
              </div>
            ))}
          </div>

          <div className="card">
            <h3>Decision</h3>
            <div className="segmented" style={{ marginBottom: 8 }}>
              <button className={outcome === "approve" ? "on" : ""} onClick={() => setOutcome("approve")}>Approve</button>
              <button className={outcome === "request_changes" ? "on" : ""} onClick={() => setOutcome("request_changes")}>Request changes</button>
              <button className={outcome === "reject" ? "on" : ""} onClick={() => setOutcome("reject")}>Reject</button>
            </div>
            <textarea placeholder={outcome === "approve" ? "Notes (optional)" : "Reason (required)"} value={reason} onChange={(e) => setReason(e.target.value)} />
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
              Submit decision
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

      {reveal && (
        <div className="modal-back">
          <div className="modal">
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
              <button className="btn primary" onClick={() => {
                const recorded = reveal.recorded;
                setReveal(null);
                if (recorded) nav(view.task.kind === "spot_check" ? "/spot-checks" : "/queue");
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
