import { useEffect, useState } from "react";
import { useL } from "../lib/useL";
import { edrak, type Doc } from "../services/edrak";
import { Icon } from "./ui/Icon";

const DONE = new Set(["indexed", "failed"]);

/** Uploaded documents with their indexing state: retry a failed file, remove any file. Refreshes while files index. */
export function DocumentList({ docs, onChange, limit }: { docs: Doc[]; onChange: (docs: Doc[]) => void; limit?: number }) {
  const L = useL();
  const [busy, setBusy] = useState<string | null>(null);
  const pending = docs.some((d) => !DONE.has(d.status));

  useEffect(() => {
    if (!pending) return;
    const t = window.setInterval(() => { void edrak.documents().then(onChange).catch(() => undefined); }, 2000);
    return () => window.clearInterval(t);
  }, [pending, onChange]);

  const act = async (id: string, fn: () => Promise<unknown>) => {
    setBusy(id);
    try { await fn(); onChange(await edrak.documents()); } catch { /* the row keeps its last state */ } finally { setBusy(null); }
  };

  const status = (d: Doc) => {
    if (d.status === "indexed") return <span className="chip c-ok">{L("Ready", "جاهز")}</span>;
    if (d.status === "failed") return <span className="chip c-bad">{L("Failed", "فشل")}</span>;
    return <span className="chip c-ins">{L("Indexing", "قيد الفهرسة")}{d.index_progress ? ` ${d.index_progress}%` : ""}</span>;
  };

  return (
    <div className="stack gap4">
      {(limit ? docs.slice(0, limit) : docs).map((d) => (
        <div key={d.document_id} className="list-row doc-row">
          <Icon name="file" size={16} />
          <div className="stack grow" style={{ minWidth: 0 }}>
            <bdi className="ellip s13">{d.filename}</bdi>
            {d.status === "failed" && d.error_message && <span className="s12 t3 ellip" title={d.error_message}>{d.error_message}</span>}
          </div>
          {status(d)}
          {d.status === "failed" && (
            <button type="button" className="btn ghost sm" disabled={busy === d.document_id}
              onClick={() => void act(d.document_id, () => edrak.retryDocument(d.document_id))}>
              <Icon name="refresh" size={13} /> {L("Retry", "إعادة")}
            </button>
          )}
          <button type="button" className="iconbtn" aria-label={L(`Remove ${d.filename}`, `إزالة ${d.filename}`)} disabled={busy === d.document_id}
            onClick={() => { if (window.confirm(L("Remove this document? Edrak will stop using it.", "إزالة هذا المستند؟ لن يستخدمه إدراك بعد الآن."))) void act(d.document_id, () => edrak.removeDocument(d.document_id)); }}>
            <Icon name="x" size={14} />
          </button>
        </div>
      ))}
    </div>
  );
}
