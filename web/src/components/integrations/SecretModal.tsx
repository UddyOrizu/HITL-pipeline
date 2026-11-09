import { useState } from "react";
import { Modal } from "../ui";
import { CopyField } from "../admin/common";

export type SecretItem = { label: string; value: string | null | undefined; hint?: string; secret?: boolean };

// Shows one-time secrets (API key, webhook secret, OAuth client secret) with copy buttons.
export function SecretModal({ title, items, note, onClose }: { title: string; items: SecretItem[]; note?: string; onClose: () => void }) {
  const [ack, setAck] = useState(false);
  const shown = items.filter((i) => i.value);
  const hasSecret = shown.some((i) => i.secret !== false);
  // Closing by accident (backdrop, Escape) would lose the secret for good, so ask first.
  const close = () => {
    if (!hasSecret || ack || window.confirm("Close without confirming you stored these? They can't be shown again.")) onClose();
  };
  return (
    <Modal title={title} onClose={close} width={620}>
      {hasSecret && (
        <div className="notice">
          <strong>Copy these now: you won't see them again.</strong> They are shown only once; a lost secret can only be
          replaced by rotating it. Store them in your app's secret manager, not in source code.
        </div>
      )}
      {note && <p className="sub">{note}</p>}
      {shown.map((i) => <CopyField key={i.label} label={i.label} value={i.value as string} hint={i.hint} />)}
      {hasSecret && (
        <label className="check">
          <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} /> I have stored these somewhere safe
        </label>
      )}
      <div className="btn-row modal-actions">
        <button className="btn primary" disabled={hasSecret && !ack} onClick={onClose}>Done</button>
      </div>
    </Modal>
  );
}
