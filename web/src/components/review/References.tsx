// Reference documents sent with the submission (client file, source material) for the reviewer to check claims
// against. Loaded on first view. Deliberately no data-section-id / data-span-id attributes: reading time here must
// not count as reading the document under review (the telemetry client observes those attributes page-wide).

import { useState } from "react";
import { api, download } from "../../api";
import { Badge, Empty, ErrorBox, label, useLoad } from "../ui";

export type RefSummary = { id: string; filename: string; render_kind: string };

type RefSpan = { id: string; kind: string; text: string };
type RefSection = { id: string; kind: string; title: string | null; spans: RefSpan[] };
type RefDoc = RefSummary & { mime_type: string; available: boolean; sections: RefSection[] };

function sectionLabel(sec: RefSection, i: number, renderKind: string) {
  if (sec.title) return sec.title;
  if (renderKind === "slides") return `Slide ${i + 1}`;
  if (renderKind === "pages") return `Page ${i + 1}`;
  return null;
}

function RefText({ doc }: { doc: RefDoc }) {
  const empty = doc.sections.every((s) => !s.spans.length);
  if (empty) return <p className="sub" style={{ fontSize: 12 }}>No text could be extracted from this file. Download the original to read it.</p>;
  return (
    <div className="ref-text" tabIndex={0} aria-label={`Extracted text of ${doc.filename}`}>
      {doc.sections.map((sec, i) => {
        const title = sectionLabel(sec, i, doc.render_kind);
        return (
          <div key={sec.id}>
            {title && doc.sections.length > 1 && <div className="ref-section-title">{title}</div>}
            {sec.spans.map((sp) => (
              <p key={sp.id} className={sp.kind === "heading" || sp.kind === "title" ? "ref-heading" : undefined}>
                {sp.kind === "list_item" ? `•  ${sp.text}` : sp.text}
              </p>
            ))}
          </div>
        );
      })}
    </div>
  );
}

export function References({ taskId, expected }: { taskId: string; expected: RefSummary[] }) {
  const { data, error, loading, reload } = useLoad(() => api<RefDoc[]>(`/v1/review/tasks/${taskId}/references`), [taskId]);
  const [dlError, setDlError] = useState<unknown>(null);
  const [downloading, setDownloading] = useState<string | null>(null);

  async function get(doc: RefDoc) {
    setDlError(null);
    setDownloading(doc.id);
    try {
      await download(`/v1/review/tasks/${taskId}/references/${doc.id}/file`, doc.filename);
    } catch (e) {
      setDlError(e);
    } finally {
      setDownloading(null);
    }
  }

  return (
    <div className="card">
      <h3>Reference documents ({data?.length ?? expected.length})</h3>
      <p className="sub" style={{ fontSize: 12, marginTop: 0 }}>
        Source material sent with this submission. Use it to check claims in the document. It is not part of what you are approving.
      </p>
      {error ? (
        <>
          <ErrorBox error={error} />
          <button className="btn small" onClick={reload}>Try again</button>
        </>
      ) : !data ? (
        <Empty>{loading ? "Loading references…" : "No references."}</Empty>
      ) : !data.length ? (
        <p className="sub">No reference documents were sent with this submission.</p>
      ) : (
        <>
          <ErrorBox error={dlError} />
          {data.map((doc, i) => (
            <div key={doc.id} className="ref-doc">
              <div className="ref-doc-head">
                <span className="ref-doc-name">{doc.filename}</span>
                <span className="btn-row" style={{ alignItems: "center" }}>
                  <Badge>{label(doc.render_kind)}</Badge>
                  <button className="btn small" disabled={!doc.available || downloading === doc.id} onClick={() => get(doc)}
                    aria-label={`Download original of ${doc.filename}`}
                    title={doc.available ? undefined : "The original file was removed under the retention policy."}>
                    {downloading === doc.id ? "Downloading…" : "Download"}
                  </button>
                </span>
              </div>
              {!doc.available && <p className="sub" style={{ fontSize: 12 }}>The original file was removed under the retention policy. Extracted text is shown if still held.</p>}
              <details open={i === 0 && data.length <= 3}>
                <summary>Extracted text</summary>
                <RefText doc={doc} />
              </details>
            </div>
          ))}
        </>
      )}
    </div>
  );
}
