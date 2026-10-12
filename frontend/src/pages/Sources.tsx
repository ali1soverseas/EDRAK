import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { PageChrome } from "../components/shell/AppShell";
import { Icon } from "../components/ui/Icon";
import { ProgressBar } from "../components/ui/Progress";
import { useI18n } from "../i18n";
import { useL } from "../lib/useL";
import { edrak, type Doc, type Preview } from "../services/edrak";

/** A document's original, its extracted content and how Edrak indexed and checked it, over a blurred page. */
function FileViewer({ id, onClose }: { id: string; onClose: () => void }) {
  const L = useL();
  const [p, setP] = useState<Preview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sheet, setSheet] = useState(0);

  useEffect(() => {
    void edrak.preview(id).then(setP).catch((e) => setError((e as Error).message));
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [id, onClose]);

  const doc = p?.document;
  return (
    <div className="viewer-scrim" onClick={onClose} role="dialog" aria-modal="true">
      <div className="viewer" onClick={(e) => e.stopPropagation()}>
        <div className="stack" style={{ minHeight: 0 }}>
          <div className="row g8" style={{ padding: "14px 20px", borderBottom: "1px solid var(--line)" }}>
            <Icon name={doc?.kind === "table" ? "layers" : "file"} size={18} />
            <bdi className="b6 grow ellip">{doc?.filename ?? L("Loading…", "جارٍ التحميل…")}</bdi>
            <a className="btn" href={edrak.fileUrl(id, true)}><Icon name="upload" size={14} /> {L("Download original", "تنزيل الأصل")}</a>
            <button type="button" className="iconbtn" onClick={onClose} aria-label={L("Close", "إغلاق")}><Icon name="x" size={16} /></button>
          </div>
          <div className="doc" style={{ flex: 1, padding: p?.view === "pdf" ? 0 : undefined }}>
            {error && <div className="empty">{error}</div>}
            {p?.view === "pdf" && <iframe title={doc?.filename} src={edrak.fileUrl(id)} />}
            {p?.view === "text" && <pre dir="auto">{p.text}{p.truncated ? "\n\n…" : ""}</pre>}
            {p?.view === "table" && (
              <div className="stack gap12">
                {p.sheets.length > 1 && (
                  <div className="row g8 wrap">{p.sheets.map((s, i) => <button key={s.name} type="button" className={`pill ${i === sheet ? "b6" : ""}`} onClick={() => setSheet(i)}>{s.name}</button>)}</div>
                )}
                <div style={{ overflow: "auto" }}>
                  <table className="tbl">
                    <thead><tr>{p.sheets[sheet]?.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
                    <tbody>{p.sheets[sheet]?.rows.map((r, i) => <tr key={i}>{r.map((v, j) => <td key={j} dir="auto">{v}</td>)}</tr>)}</tbody>
                  </table>
                </div>
              </div>
            )}
            {p?.view === "unavailable" && <div className="empty">{p.message}</div>}
          </div>
        </div>
        <aside className="stack gap12">
          <span className="eyebrow">{L("How Edrak read it", "كيف قرأه إدراك")}</span>
          {doc && (
            <div className="stack gap4 s13">
              <span>{L("Status", "الحالة")}: <b>{doc.status}</b></span>
              <span>{L("Safety check", "فحص الأمان")}: <b>{doc.guardrail_status}</b></span>
              {doc.retrievability != null && <span>{L("Findable by search", "قابلية العثور")}: <b>{Math.round(doc.retrievability * 100)}%</b></span>}
              {doc.guardrail_notes.map((n) => <span key={n} className="chip c-ins" style={{ whiteSpace: "normal" }}>{n}</span>)}
            </div>
          )}
          <span className="eyebrow">{L(`${p?.chunks.length ?? 0} passages`, `${p?.chunks.length ?? 0} مقطع`)}</span>
          {p?.chunks.map((c) => (
            <div key={c.id} className="panel stack gap4" style={{ padding: 10, opacity: c.excluded ? 0.6 : 1 }}>
              <span className="s12 muted">{c.locator ?? `#${c.chunk_index + 1}`}{c.excluded ? ` · ${L("kept out of search", "مستبعد من البحث")}` : ""}</span>
              <span className="s12" dir="auto">{c.content.slice(0, 220)}{c.content.length > 220 ? "…" : ""}</span>
              {c.flags.map((f) => <span key={f} className="s12" style={{ color: "#a15c00" }}>{f}</span>)}
            </div>
          ))}
        </aside>
      </div>
    </div>
  );
}

export function Sources() {
  const L = useL();
  const { formatWhen } = useI18n();
  const [params, setParams] = useSearchParams();
  const [docs, setDocs] = useState<Doc[] | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [drag, setDrag] = useState(false);
  const viewing = params.get("doc");

  const load = useCallback(() => void edrak.documents().then(setDocs).catch(() => setDocs([])), []);
  useEffect(load, [load]);
  useEffect(() => {
    if (!docs?.some((d) => d.status === "indexing")) return;
    const id = setTimeout(load, 1500);
    return () => clearTimeout(id);
  }, [docs, load]);

  const upload = async (files: File[]) => {
    if (!files.length) return;
    const res = await edrak.upload(files);
    setErrors(res.errors.map((e) => `${e.filename}: ${e.message}`));
    load();
  };

  return (
    <>
      <PageChrome crumbs={[{ label: L("Knowledge", "المعرفة") }, { label: L("Data sources", "مصادر البيانات") }]} title={L("Data sources", "مصادر البيانات")} />
      <div className="stack gap16" style={{ maxWidth: 1180 }}>
        <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
          <div><div className="eyebrow">{L("Data sources", "مصادر البيانات")}</div><h1 style={{ margin: "6px 0 0" }}>{L("What Edrak is allowed to read.", "ما يُسمح لإدراك بقراءته.")}</h1></div>
          <Link to="/company" className="btn">{L("Company profile", "ملف الشركة")}</Link>
        </div>

        <label className="panel row g12" style={{ borderStyle: "dashed", cursor: "pointer", background: drag ? "rgba(10,60,240,.05)" : undefined }}
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); void upload(Array.from(e.dataTransfer.files)); }}>
          <span className="sq" style={{ width: 44, height: 44, borderRadius: 10, background: "rgba(10,60,240,.08)", display: "inline-flex", alignItems: "center", justifyContent: "center", color: "var(--blue)" }}><Icon name="upload" size={20} /></span>
          <div className="grow stack">
            <b>{L("Drop files here, or choose them", "أسقط الملفات هنا أو اخترها")}</b>
            <span className="s13 muted">{L("PDF, Word, Excel, CSV, Markdown, text. Checked, chunked and indexed for the Internal worker. Files never leave your workspace.", "PDF وWord وExcel وCSV ونص. تُفحص وتُفهرس لعامل البيانات الداخلية ولا تغادر مساحة عملك.")}</span>
          </div>
          <span className="btn"><Icon name="plus" size={14} /> {L("Add files", "أضف ملفات")}</span>
          <input type="file" multiple hidden accept=".pdf,.docx,.xlsx,.csv,.md,.txt,.json" onChange={(e) => void upload(Array.from(e.target.files ?? []))} />
        </label>
        {errors.map((e) => <div key={e} className="chip c-bad" style={{ whiteSpace: "normal" }}>{e}</div>)}

        <div className="panel" style={{ padding: 0 }}>
          <table className="tbl">
            <thead><tr><th>{L("Document", "المستند")}</th><th>{L("Status", "الحالة")}</th><th>{L("Safety", "الأمان")}</th><th>{L("Added", "أُضيف")}</th><th>{L("Used in runs", "استُخدم في")}</th><th /></tr></thead>
            <tbody>
              {docs?.length === 0 && <tr><td colSpan={6}><div className="empty">{L("No documents yet.", "لا مستندات بعد.")}</div></td></tr>}
              {docs?.map((d) => (
                <tr key={d.document_id}>
                  <td>
                    <button type="button" className="row g8" style={{ background: "none", border: 0, cursor: "pointer", textAlign: "start", padding: 0 }} onClick={() => setParams({ doc: d.document_id })}>
                      <Icon name={d.kind === "table" ? "layers" : "file"} size={16} />
                      <span className="stack"><bdi className="b6">{d.filename}</bdi><span className="s12 muted">{d.size ? `${d.size.value} ${d.size.unit}` : ""}{d.is_synthetic ? ` · ${L("synthetic", "اصطناعي")}` : ""}</span></span>
                    </button>
                  </td>
                  <td style={{ minWidth: 140 }}>
                    {d.status === "indexing" ? (<div className="stack gap4"><span className="s12">{L("Indexing", "فهرسة")} {d.index_progress ?? 0}%</span><ProgressBar value={d.index_progress ?? 0} /></div>)
                      : d.status === "failed" ? <span className="chip c-bad" title={d.error_message ?? ""}>{L("Failed to parse", "تعذرت القراءة")}</span>
                      : <span className="chip c-ok">{L("Indexed", "مفهرس")}</span>}
                  </td>
                  <td>{d.guardrail_status === "clean" ? <span className="s12 muted">{L("Clean", "سليم")}</span> : <span className={`chip ${d.guardrail_status === "quarantined" ? "c-bad" : "c-ins"}`}>{d.guardrail_status}</span>}</td>
                  <td className="s13 muted">{d.added_by ?? ""}<br />{d.added_at ? formatWhen(d.added_at) : ""}</td>
                  <td>{d.used_in_runs}</td>
                  <td className="row g8">
                    <button type="button" className="btn" onClick={() => setParams({ doc: d.document_id })}>{L("View", "عرض")}</button>
                    {d.status === "failed" && <button type="button" className="btn ghost" onClick={() => void edrak.retryDocument(d.document_id).then(load)}>{L("Retry", "إعادة")}</button>}
                    <button type="button" className="iconbtn" aria-label={L("Remove", "إزالة")} onClick={() => { if (confirm(L("Remove this document?", "إزالة هذا المستند؟"))) void edrak.removeDocument(d.document_id).then(load); }}><Icon name="x" size={14} /></button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="s12 muted"><Icon name="info" size={12} /> {L("Failed files can be retried or replaced. Passages that try to instruct the AI are kept out of search.", "يمكن إعادة الملفات الفاشلة. المقاطع التي تحاول توجيه الذكاء الاصطناعي تُستبعد من البحث.")}</p>
      </div>
      {viewing && <FileViewer id={viewing} onClose={() => setParams({})} />}
    </>
  );
}
