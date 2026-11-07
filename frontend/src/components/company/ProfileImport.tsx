import { useMemo, useRef, useState, type DragEvent } from "react";
import { usePhrase } from "../../lib/phrase";
import { useL } from "../../lib/useL";
import {
  applyDraft, appliedCount, defaultChoices, hostLabel, importErrorMessage, importRows,
  type ImportableForm, type ImportField, type ImportRow,
} from "../../lib/profileImport";
import { activeRunId, researchErrorMessage } from "../../lib/research";
import { edrak, type ProfileDraft } from "../../services/edrak";
import { Chip } from "../ui/Chip";
import { Segmented } from "../ui/Controls";
import { Icon } from "../ui/Icon";
import { ThinkingMark } from "../ui/ThinkingMark";

type Mode = "url" | "pdf";

type View =
  | { kind: "idle" }
  | { kind: "busy"; what: string }
  | { kind: "error"; message: string }
  | { kind: "review"; draft: ProfileDraft; origin: string; icon: "globe" | "file"; chosen: Set<ImportField> }
  | { kind: "done"; count: number };

/** Field names as the person knows them, in the order of the form. [English, Arabic] */
const LABELS: Record<ImportField, readonly [string, string]> = {
  name: ["Company name", "اسم الشركة"],
  aliases: ["Also known as", "يُعرف أيضًا باسم"],
  notes: ["What the company does", "ماذا تفعل الشركة"],
  products: ["Products and services", "المنتجات والخدمات"],
  industry: ["Industry", "المجال"],
  markets: ["Markets you sell in", "أسواق تبيع فيها"],
  goals: ["Goals", "الأهداف"],
  links: ["Website and accounts", "الموقع والحسابات"],
};
/** Account names are brand names; only "website" needs translating. */
const PLATFORM_NAMES: Record<string, readonly [string, string]> = {
  website: ["Website", "الموقع"], x: ["X", "X"], facebook: ["Facebook", "Facebook"], instagram: ["Instagram", "Instagram"],
  linkedin: ["LinkedIn", "LinkedIn"], youtube: ["YouTube", "YouTube"], tiktok: ["TikTok", "TikTok"],
};
const SOURCE_TYPES: Record<ProfileDraft["sources"][number]["type"], readonly [string, string]> = {
  web_page: ["Web page", "صفحة ويب"],
  pdf_page: ["PDF page", "صفحة PDF"],
  page_metadata: ["Page details", "بيانات الصفحة"],
};

interface Props<T extends ImportableForm> {
  form: T;
  /** The form with the chosen fields filled in. The caller keeps it like any other edit. */
  onApply: (next: T) => void;
  industryLabel: (key: string) => string;
  /** When given, reading a website can also start company research in the background. */
  research?: { on: boolean; onChange: (on: boolean) => void };
}

/**
 * Start company setup from a website or a PDF. The draft is read, shown for review, and only the
 * ticked fields are added to the form. Nothing is saved here: the setup steps save as they always do.
 */
export function ProfileImport<T extends ImportableForm>({ form, onApply, industryLabel, research }: Props<T>) {
  const L = useL();
  const [mode, setMode] = useState<Mode>("url");
  const [url, setUrl] = useState(form.website);
  const [view, setView] = useState<View>({ kind: "idle" });
  const [over, setOver] = useState(false);
  const [researchNote, setResearchNote] = useState<{ ok: boolean; text: string } | null>(null);
  const run = useRef(0); // which request the screen is waiting for; a cancelled one is ignored when it lands
  const busy = view.kind === "busy";

  const rows = useMemo(
    () => (view.kind === "review" ? importRows(form, view.draft, industryLabel) : []),
    [view, form, industryLabel],
  );

  const read = async (what: string, origin: string, icon: "globe" | "file", call: () => Promise<ProfileDraft>) => {
    const id = ++run.current;
    setView({ kind: "busy", what });
    try {
      const draft = await call();
      if (id !== run.current) return;
      setView({ kind: "review", draft, origin, icon, chosen: defaultChoices(importRows(form, draft, industryLabel)) });
    } catch (e) {
      if (id !== run.current) return;
      setView({ kind: "error", message: importErrorMessage(e, L) });
    }
  };

  const readSite = () => {
    const typed = url.trim();
    if (!typed || busy) return;
    const host = hostLabel(typed);
    if (research?.on) void startResearch(typed);
    void read(L(`Reading ${host}…`, `جارٍ قراءة ${host}…`), host, "globe", () => edrak.importFromUrl(typed));
  };
  /** Company research runs on its own; it never holds up the profile draft, and a refusal only shows a note. */
  const startResearch = async (typed: string) => {
    setResearchNote(null);
    try {
      await edrak.startResearch(typed);
      setResearchNote({ ok: true, text: L("Research started in the background. You will see what it finds in the last step.", "بدأ البحث في الخلفية. سترى ما يجده في الخطوة الأخيرة.") });
    } catch (e) {
      const running = activeRunId(e) !== null;
      setResearchNote({ ok: running, text: running ? L("Research is already running for your company.", "البحث جارٍ بالفعل لشركتك.") : researchErrorMessage(e, L) });
    }
  };
  const readPdf = (file: File | undefined) => {
    if (!file || busy) return;
    void read(L(`Reading ${file.name}…`, `جارٍ قراءة ${file.name}…`), file.name, "file", () => edrak.importFromPdf(file));
  };
  const cancel = () => {
    run.current += 1;
    setView({ kind: "idle" });
  };
  const toggle = (key: ImportField) =>
    setView((cur) => {
      if (cur.kind !== "review") return cur;
      const chosen = new Set(cur.chosen);
      if (!chosen.delete(key)) chosen.add(key);
      return { ...cur, chosen };
    });
  const apply = () => {
    if (view.kind !== "review") return;
    onApply(applyDraft(form, view.draft, view.chosen));
    setView({ kind: "done", count: appliedCount(rows, view.chosen) });
  };
  const onDrop = (event: DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    setOver(false);
    readPdf(event.dataTransfer.files[0]);
  };

  const picking = view.kind === "idle" || view.kind === "error";

  return (
    <section className="import" aria-label={L("Start from your website or a PDF", "ابدأ من موقعك أو ملف PDF")}>
      <div className="row g12" style={{ alignItems: "flex-start" }}>
        <span className="import-mark"><Icon name="sparkle" size={15} /></span>
        <div className="stack gap4 grow">
          <b>{L("Start from what you already have", "ابدأ مما لديك بالفعل")}</b>
          <span className="s13 t2">
            {L("Edrak reads your website or a company PDF and drafts this profile. You review it before anything is added.",
              "يقرأ إدراك موقعك أو ملف PDF للشركة ويكتب مسودة هذا الملف. تراجعها بنفسك قبل إضافة أي شيء.")}
          </span>
        </div>
      </div>

      {(picking || busy) && (
        <>
          <div>
            <Segmented<Mode>
              label={L("Where to read from", "من أين نقرأ")}
              value={mode}
              onChange={(next) => { if (!busy) { setMode(next); setView({ kind: "idle" }); } }}
              options={[
                { value: "url", label: <><Icon name="globe" size={14} /> {L("Website", "الموقع الإلكتروني")}</> },
                { value: "pdf", label: <><Icon name="file" size={14} /> PDF</> },
              ]}
            />
          </div>

          {mode === "url" ? (
            <>
              <div className="row g8 import-url">
                <input
                  className="input" dir="ltr" inputMode="url" autoComplete="url" placeholder="example.com" value={url} disabled={busy}
                  aria-label={L("Company website", "موقع الشركة")}
                  onChange={(e) => setUrl(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); readSite(); } }}
                />
                <button type="button" className="btn pri" disabled={busy || !url.trim()} onClick={readSite}>
                  {L("Read website", "اقرأ الموقع")} <Icon name="arrow" size={15} flip />
                </button>
              </div>
              {research && (
                <label className="research-opt s13">
                  <input type="checkbox" checked={research.on} onChange={(e) => research.onChange(e.target.checked)} />
                  <span>
                    <b>{L("Also research this company in the background", "ابحث عن هذه الشركة في الخلفية أيضًا")}</b>
                    <span className="t2"> — {L("leadership, ownership, financials, locations and more, each with its source. About 5 minutes; you review it at the end.", "القيادة والملكية والبيانات المالية والمواقع وغيرها، كل منها مع مصدره. نحو 5 دقائق وتراجعها في النهاية.")}</span>
                  </span>
                </label>
              )}
              {researchNote && <span className={`s13 ${researchNote.ok ? "t2" : ""}`} role="status" style={researchNote.ok ? undefined : { color: "var(--bad)" }}>{researchNote.text}</span>}
            </>
          ) : (
            <label
              className={`dropzone ${over ? "over" : ""}`.trim()}
              aria-disabled={busy}
              onDragOver={(e) => { e.preventDefault(); if (!busy) setOver(true); }}
              onDragLeave={() => setOver(false)}
              onDrop={onDrop}
            >
              <span className="doc-icon"><Icon name="upload" size={18} /></span>
              <span className="stack grow">
                <b className="s13">{L("Drop a PDF here, or choose one", "أفلت ملف PDF هنا، أو اختر ملفًا")}</b>
                <span className="s12 t3">{L("A company profile, a brochure or a deck.", "ملف تعريفي أو كتيّب أو عرض تقديمي.")}</span>
              </span>
              <input
                type="file" className="sr-only" accept=".pdf,application/pdf" disabled={busy}
                aria-label={L("Choose a PDF", "اختر ملف PDF")}
                onChange={(e) => { readPdf(e.target.files?.[0]); e.target.value = ""; }}
              />
            </label>
          )}
        </>
      )}

      {view.kind === "error" && (
        <div className="form-error" role="alert">
          <Icon name="warning" size={16} />
          <span className="grow" dir="auto">{view.message}</span>
        </div>
      )}

      {view.kind === "busy" && (
        <div className="import-busy" role="status" aria-live="polite">
          <ThinkingMark size={30} color="var(--blue)" />
          <div className="stack gap4 grow">
            <b className="s13" dir="auto">{view.what}</b>
            <span className="s12 t3">{L("Collecting the text and drafting the profile. This can take a little while.", "يجمع إدراك النص ويكتب المسودة. قد يستغرق ذلك قليلًا.")}</span>
          </div>
          <button type="button" className="btn ghost sm" onClick={cancel}>{L("Cancel", "إلغاء")}</button>
        </div>
      )}

      {view.kind === "review" && (
        <Review
          draft={view.draft} origin={view.origin} icon={view.icon} rows={rows} chosen={view.chosen}
          onToggle={toggle} onApply={apply} onDiscard={() => setView({ kind: "idle" })}
        />
      )}

      {view.kind === "done" && (
        <div className="import-busy" role="status">
          <Chip tone="ok" icon="check">{L("Added to your profile", "أُضيف إلى ملفك")}</Chip>
          <span className="s13 t2 grow">
            {L(`${view.count} ${view.count === 1 ? "entry" : "entries"} filled in. Look through each step as you continue.`,
              `تمت تعبئة ${view.count} من الإدخالات. راجع كل خطوة وأنت تتابع.`)}
          </span>
          <button type="button" className="btn ghost sm" onClick={() => setView({ kind: "idle" })}>{L("Import another", "استيراد مصدر آخر")}</button>
        </div>
      )}
    </section>
  );
}

/* ---------------------------------------------------------------- the draft, for review */

interface ReviewProps {
  draft: ProfileDraft;
  origin: string;
  icon: "globe" | "file";
  rows: ImportRow[];
  chosen: Set<ImportField>;
  onToggle: (key: ImportField) => void;
  onApply: () => void;
  onDiscard: () => void;
}

function Review({ draft, origin, icon, rows, chosen, onToggle, onApply, onDiscard }: ReviewProps) {
  const L = useL();
  const phrase = usePhrase();
  const warnings = draft.warning_items?.length
    ? draft.warning_items.map((item) => phrase(item.message, item.key, item.params))
    : draft.warnings;
  const sourceById = new Map(draft.sources.map((s) => [s.id, s]));
  const sourceTitle = (id: string) => {
    const s = sourceById.get(id);
    return s ? `${L(...SOURCE_TYPES[s.type])}: ${s.ref}${s.title ? ` (${s.title})` : ""}` : id;
  };
  const gaps = draft.gaps.filter((g): g is ImportField => g in LABELS);
  const canApply = rows.some((r) => chosen.has(r.key) && r.adds.length > 0);

  return (
    <div className="import-review rise">
      <div className="row g8 wrap">
        <b>{L("Here is the draft", "هذه هي المسودة")}</b>
        <Chip tone="blue" icon={icon}><bdi>{L(`From ${origin}`, `من ${origin}`)}</bdi></Chip>
      </div>

      {rows.length === 0 ? (
        <div className="empty">{L("Edrak did not find anything it could add from this source.", "لم يجد إدراك ما يمكن إضافته من هذا المصدر.")}</div>
      ) : (
        <>
          <p className="s13 t2" style={{ margin: 0 }}>
            {L("Tick what to add to your profile. Nothing is saved until you continue.", "حدد ما تريد إضافته إلى ملفك. لا يُحفظ شيء حتى تتابع.")}
          </p>
          <ul className="import-rows">
            {rows.map((row) => {
              const known = row.adds.length === 0;
              const on = chosen.has(row.key) && !known;
              const labelId = `import-${row.key}`;
              return (
                <li key={row.key} className={`import-row ${known ? "is-same" : ""}`.trim()}>
                  <button
                    type="button" role="checkbox" aria-checked={on} aria-labelledby={labelId} disabled={known}
                    className={`chk ${on ? "on" : ""}`.trim()} onClick={() => onToggle(row.key)}
                  >
                    {on && <Icon name="check" size={12} stroke={2.6} />}
                  </button>
                  <div className="stack gap4">
                    <span id={labelId} className="s12 t3 b6">{L(...LABELS[row.key])}</span>
                    {row.kind === "text" ? (
                      <span dir="auto">{row.proposed[0]}</span>
                    ) : row.kind === "links" ? (
                      <div className="row g8 wrap">
                        {row.linkItems?.map((link) => (
                          <span key={link.platform} className={`tag ${link.isNew ? "" : "known"}`.trim()}>
                            <b>{L(...(PLATFORM_NAMES[link.platform] ?? [link.platform, link.platform]))}</b>
                            <bdi dir="ltr">{link.url}</bdi>
                          </span>
                        ))}
                      </div>
                    ) : (
                      <div className="row g8 wrap">
                        {row.proposed.map((value) => (
                          <span key={value} className={`tag ${row.adds.includes(value) ? "" : "known"}`.trim()} dir="auto">{value}</span>
                        ))}
                      </div>
                    )}
                    {row.replaces && <span className="s12 t3">{L("Replaces", "يستبدل")}: <bdi>{row.replaces}</bdi></span>}
                    {known && (
                      <span className="s12 t3">
                        {row.kind === "links"
                          ? L("Those boxes are already filled in. Links only fill empty ones.", "هذه الخانات معبأة بالفعل. تملأ الروابط الخانات الفارغة فقط.")
                          : L("Already in your profile", "موجود بالفعل في ملفك")}
                      </span>
                    )}
                  </div>
                  <span className="import-src">
                    {row.sources.map((id) => <span key={id} className="ev" title={sourceTitle(id)}>{id}</span>)}
                  </span>
                </li>
              );
            })}
          </ul>
        </>
      )}

      {warnings.map((warning) => (
        <div key={warning} className="form-warn" role="note">
          <Icon name="warning" size={16} />
          <span className="grow" dir="auto">{warning}</span>
        </div>
      ))}

      {gaps.length > 0 && (
        <div className="row g8 wrap">
          <span className="s12 t3">{L("Not found in this source", "غير موجود في هذا المصدر")}:</span>
          {gaps.map((g) => <span key={g} className="chip c-draft">{L(...LABELS[g])}</span>)}
        </div>
      )}

      {draft.sources.length > 0 && (
        <details className="import-sources">
          <summary className="s12 t3">{L(`Sources read (${draft.sources.length})`, `المصادر المقروءة (${draft.sources.length})`)}</summary>
          <ul>
            {draft.sources.map((s) => (
              <li key={s.id} className="row g8">
                <span className="ev">{s.id}</span>
                <span className="s12 t3 none">{L(...SOURCE_TYPES[s.type])}</span>
                {/^https?:\/\//.test(s.ref)
                  ? <a className="s12 grow ellip" href={s.ref} target="_blank" rel="noreferrer" dir="ltr">{s.ref}</a>
                  : <bdi className="s12 grow ellip">{s.ref}</bdi>}
              </li>
            ))}
          </ul>
        </details>
      )}

      <div className="row g8">
        <button type="button" className="btn pri" disabled={!canApply} onClick={onApply}><Icon name="check" size={15} /> {L("Add to profile", "أضف إلى الملف")}</button>
        <button type="button" className="btn ghost" onClick={onDiscard}>{L("Discard", "تجاهل")}</button>
      </div>
    </div>
  );
}
