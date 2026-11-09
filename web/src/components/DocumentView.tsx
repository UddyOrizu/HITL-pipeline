// Faithful rendered view of the normalised document: pages as pages, slides as slides, documents as a page of text.
// Risk flags, comments and citations are overlaid on the stable span ids. Every passage is keyboard-selectable
// (Tab to it, Enter or Space to select).

import "../pages/review.css";

export type SpanT = { id: string; kind: string; text: string; location: any };
export type SectionT = { id: string; kind: string; title: string | null; location: any; spans: SpanT[] };
export type FlagT = { id: string; span_id: string; category: string; severity: string; rationale: string; mandatory: boolean; opened: boolean; acknowledged: boolean };
export type CommentT = { id: string; span_id: string | null; kind: string; body: string; suggested_text: string | null; created_at: string };

const SEV = { high: 2, medium: 1, low: 0 } as Record<string, number>;

// Enter or Space activates a non-button element that has role="button" (as a native button would).
export function activateOnKey(fn: () => void) {
  return (e: React.KeyboardEvent<HTMLElement>) => {
    if (e.target !== e.currentTarget) return;
    if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") {
      e.preventDefault();
      fn();
    }
  };
}

function SpanEl({ span, flags, selected, cited, commented, onSelect }: {
  span: SpanT; flags: FlagT[]; selected: boolean; cited: boolean; commented: boolean; onSelect: (id: string) => void;
}) {
  const top = [...flags].sort((a, b) => SEV[b.severity] - SEV[a.severity])[0];
  const cls = [
    "span", `span-${span.kind}`, top ? `flag-${top.severity}` : "", selected ? "selected" : "", cited ? "cited" : "",
    commented ? "has-comment" : "",
  ].join(" ");
  const notes = [
    top ? `${top.severity} risk flag: ${top.category.replace(/_/g, " ")}` : "",
    cited ? "cited in decision" : "",
    commented ? "has your comment" : "",
  ].filter(Boolean);
  return (
    <div className={cls} data-span-id={span.id} role="button" tabIndex={0} aria-pressed={selected}
      aria-label={notes.length ? `${span.text} (${notes.join(", ")})` : undefined}
      onClick={() => onSelect(span.id)} onKeyDown={activateOnKey(() => onSelect(span.id))}
      title={top ? `${top.category.replace(/_/g, " ")}: ${top.rationale}` : undefined}>
      {span.text}
    </div>
  );
}

export function DocumentView({ renderKind, sections, flags, comments, cited, selected, onSelect }: {
  renderKind: string; sections: SectionT[]; flags: FlagT[]; comments: CommentT[]; cited: Set<string>;
  selected: string | null; onSelect: (id: string) => void;
}) {
  const bySpan = new Map<string, FlagT[]>();
  flags.forEach((f) => bySpan.set(f.span_id, [...(bySpan.get(f.span_id) || []), f]));
  const commented = new Set(comments.map((c) => c.span_id));
  const span = (s: SpanT) => (
    <SpanEl key={s.id} span={s} flags={bySpan.get(s.id) || []} selected={selected === s.id} cited={cited.has(s.id)}
      commented={commented.has(s.id)} onSelect={onSelect} />
  );

  if (renderKind === "slides") {
    return (
      <div>
        {sections.map((sec, i) => {
          const notes = sec.spans.filter((s) => s.kind === "notes");
          return (
            <div key={sec.id} data-section-id={sec.id}>
              <div className="slide">
                <div className="slide-num">Slide {sec.location?.slide ?? i + 1}</div>
                {sec.spans.filter((s) => s.kind !== "notes").map(span)}
              </div>
              {notes.length > 0 && (
                <div className="slide-notes"><strong>Speaker notes</strong>{notes.map(span)}</div>
              )}
            </div>
          );
        })}
      </div>
    );
  }
  if (renderKind === "pages") {
    return (
      <div>
        {sections.map((sec, i) => (
          <div key={sec.id} className="page" data-section-id={sec.id}>
            {sec.spans.map(span)}
            <div className="page-num">Page {sec.location?.page ?? i + 1}</div>
          </div>
        ))}
      </div>
    );
  }
  return (
    <div className="doc">
      {sections.map((sec) => (
        <div key={sec.id} className="doc-section" data-section-id={sec.id}>
          {sec.title && sec.title === "Document comments" && <div className="doc-section-title">{sec.title}</div>}
          {sec.spans.map(span)}
        </div>
      ))}
    </div>
  );
}
