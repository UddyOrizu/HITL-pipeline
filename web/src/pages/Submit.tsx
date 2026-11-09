import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api } from "../api";
import { Empty, ErrorBox, useLoad } from "../components/ui";
import "./review.css";

type WF = { id: string; name: string; stages: string[] };
// Sent by My submissions' "Revise and submit again".
type Revision = { previous_submission_id: string; title: string | null; workflow_id: string | null };

function readRevision(state: unknown): Revision | null {
  const s = state as Partial<Revision> | null;
  if (!s || typeof s.previous_submission_id !== "string" || !s.previous_submission_id) return null;
  return {
    previous_submission_id: s.previous_submission_id,
    title: typeof s.title === "string" ? s.title : null,
    workflow_id: typeof s.workflow_id === "string" ? s.workflow_id : null,
  };
}

const ACCEPT = ".txt,.docx,.pptx,.ppt,.pdf";

export default function Submit() {
  const nav = useNavigate();
  const location = useLocation();
  const [revision, setRevision] = useState<Revision | null>(() => readRevision(location.state));
  const { data: workflows, error: wfErr } = useLoad(() => api<WF[]>("/v1/portal/workflows"));
  const [mode, setMode] = useState<"file" | "text">("file");
  const [workflowId, setWorkflowId] = useState(revision?.workflow_id ?? "");
  const [title, setTitle] = useState(revision?.title ?? "");
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");
  const [refs, setRefs] = useState<File[]>([]);
  const [risk, setRisk] = useState("medium");
  const [origin, setOrigin] = useState("ai");
  const [model, setModel] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  // The revision's workflow may since have been removed or closed to web submissions: fall back to the first.
  const wfKnown = !workflowId || !workflows || workflows.some((w) => w.id === workflowId);
  const wf = (wfKnown && workflowId) || workflows?.[0]?.id || "";

  function clearRevision() {
    setRevision(null);
    setTitle("");
    setWorkflowId("");
    nav(location.pathname + location.search, { replace: true, state: null }); // a reload shouldn't bring it back
  }
  const ready = wf && (mode === "file" ? !!file : text.trim().length > 0);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    const form = new FormData();
    form.set("workflow_id", wf);
    form.set("risk_tier", risk);
    form.set("content_origin", origin);
    if (model) form.set("ai_model", model);
    form.set("metadata", JSON.stringify(title ? { title } : {}));
    if (revision) form.set("previous_submission_id", revision.previous_submission_id);
    if (mode === "file" && file) form.set("file", file);
    else form.set("text", text);
    refs.forEach((r) => form.append("reference_files", r));
    try {
      const sub = await api<{ id: string }>("/v1/portal/submissions", { body: form });
      nav(`/submissions?new=${sub.id}`);
    } catch (e2) {
      setErr(e2);
      setBusy(false);
    }
  }

  if (wfErr) return <ErrorBox error={wfErr} />;
  if (!workflows) return <Empty>Loading…</Empty>;
  if (!workflows.length) return <Empty>No workflows accept web submissions yet. Ask an admin to set one up.</Empty>;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Submit for review</h1>
          <div className="sub">Send a document or text to a reviewer. You'll see the decision and evidence under My submissions.</div>
        </div>
      </div>
      {revision && (
        <div className="notice" role="status" style={{ maxWidth: 760, boxSizing: "border-box" }}>
          <strong>Revision of {revision.title || <span className="mono">{revision.previous_submission_id.slice(0, 8)}</span>}.</strong>{" "}
          This will be recorded as a revised version of your earlier submission.{" "}
          {!wfKnown && "The original workflow is no longer available, so choose another. "}
          <button type="button" className="linklike" onClick={clearRevision}>Not a revision? Submit as new</button>
        </div>
      )}
      <form className="card" style={{ maxWidth: 760 }} onSubmit={submit}>
        <div className="field-row">
          <div>
            <label>Workflow</label>
            <select value={wf} onChange={(e) => setWorkflowId(e.target.value)}>
              {workflows.map((w) => <option key={w.id} value={w.id}>{w.name} ({w.stages.join(" → ")})</option>)}
            </select>
          </div>
          <div>
            <label>Title (optional)</label>
            <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Client letter – Hartley" />
          </div>
        </div>

        <label>Content</label>
        <div className="segmented" style={{ marginBottom: 8 }}>
          <button type="button" className={mode === "file" ? "on" : ""} onClick={() => setMode("file")}>Upload a file</button>
          <button type="button" className={mode === "text" ? "on" : ""} onClick={() => setMode("text")}>Paste text</button>
        </div>
        {mode === "file" ? (
          <label className="dropzone">
            <input type="file" accept={ACCEPT} onChange={(e) => setFile(e.target.files?.[0] || null)} />
            {file ? <><strong>{file.name}</strong> <span className="sub">{Math.ceil(file.size / 1024)} KB · click to change</span></>
              : <><strong>Choose a file</strong> <span className="sub">.docx, .pptx, .ppt, .pdf or .txt</span></>}
          </label>
        ) : (
          <textarea rows={10} value={text} onChange={(e) => setText(e.target.value)} placeholder="Paste the content to review" />
        )}

        <div className="field-row">
          <div>
            <label>Risk tier</label>
            <select value={risk} onChange={(e) => setRisk(e.target.value)}>
              <option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option>
            </select>
          </div>
          <div>
            <label>Written by</label>
            <select value={origin} onChange={(e) => setOrigin(e.target.value)}>
              <option value="ai">AI</option><option value="human">A person</option><option value="mixed">Both</option>
            </select>
          </div>
          <div>
            <label>AI model (optional)</label>
            <input value={model} onChange={(e) => setModel(e.target.value)} placeholder="e.g. claude-sonnet-5-5" disabled={origin === "human"} />
          </div>
        </div>

        <label>Reference documents (optional)</label>
        <input type="file" multiple accept={ACCEPT} onChange={(e) => setRefs(Array.from(e.target.files || []))} />
        <p className="sub" style={{ fontSize: 12, marginTop: 4 }}>Source material the reviewer can check claims against, such as a client file.</p>

        <ErrorBox error={err} />
        <div className="btn-row" style={{ marginTop: 14 }}>
          <button className="btn primary" disabled={!ready || busy}>{busy ? "Submitting…" : "Submit for review"}</button>
        </div>
      </form>
    </>
  );
}
