import { useEffect, useMemo, useState } from "react";
import { useI18n } from "../../i18n";
import { useAsync } from "../../lib/hooks";
import {
  CONFIDENCE, STATUS_FILTERS, allFacts, clearFactIds, countByStatus, editPayload, editText, filterGrouped, keepOne, keyLabel,
  listItems, researchErrorMessage, sourceLabel, tierLabel, toggleId,
} from "../../lib/research";
import { useL } from "../../lib/useL";
import { edrak, type FactCategory, type FactStatus, type ResearchFact } from "../../services/edrak";
import { Segmented } from "../ui/Controls";
import { Icon } from "../ui/Icon";

type Tab = FactStatus | "all";
/** Runs a change, then reloads the list. Resolves to false (with the error shown) when it failed. */
type Act = (work: () => Promise<unknown>) => Promise<boolean>;

function ValueView({ fact }: { fact: ResearchFact }) {
  if (fact.value_type === "list") {
    const items = listItems(fact.value);
    return <div className="row g6 wrap">{items.map((x, i) => <span key={`${x}-${i}`} className="tag" dir="auto">{x}</span>)}</div>;
  }
  if (fact.value_type === "url") return <a className="fact-value" href={fact.value} target="_blank" rel="noreferrer" dir="ltr">{fact.value}</a>;
  return <span className="fact-value" dir="auto">{fact.value}</span>;
}

interface RowProps {
  fact: ResearchFact;
  selected: boolean;
  busy: boolean;
  onSelect: () => void;
  act: Act;
  /** Set for a fact that disagrees with others: approve this one and reject the rest. */
  onKeep?: () => void;
}

function FactRow({ fact, selected, busy, onSelect, act, onKeep }: RowProps) {
  const L = useL();
  const { lang } = useI18n();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  const label = keyLabel(fact, lang);
  const payload = editing ? editPayload(fact, text) : null;

  const startEdit = () => { setText(editText(fact)); setEditing(true); };
  const save = () => {
    if (!payload) return;
    void act(() => edrak.editFact(fact.id, payload.value, payload.valueType)).then((ok) => { if (ok) setEditing(false); });
  };
  const setStatus = (status: "approved" | "rejected") => void act(() => edrak.setFactStatus(fact.id, status));

  return (
    <div className={`fact s-${fact.status}`.trim()}>
      <input type="checkbox" className="fact-pick" checked={selected} onChange={onSelect}
        aria-label={L(`Select ${label}`, `تحديد ${label}`)} />
      <div className="fact-main">
        <div className="row g8 wrap">
          <b className="fact-key" dir="auto">{label}</b>
          {fact.category === "other" && fact.proposed_category && <span className="chip c-line">{fact.proposed_category}</span>}
          <span className={`chip ${fact.source_tier === "third_party" ? "c-ins" : "c-line"}`}>{tierLabel(fact.source_tier, L)}</span>
          {fact.confidence !== "high" && <span className="chip c-await">{L(...CONFIDENCE[fact.confidence])}</span>}
          {fact.edited_by_user && <span className="chip c-blue">{L("Edited", "معدّل")}</span>}
          {fact.status === "approved" && <span className="chip c-ok"><Icon name="check" size={12} /> {L("Approved", "معتمد")}</span>}
          {fact.status === "rejected" && <span className="chip c-bad">{L("Rejected", "مرفوض")}</span>}
        </div>

        {editing ? (
          <div className="stack gap8">
            <textarea className="textarea" value={text} onChange={(e) => setText(e.target.value)} dir="auto" autoFocus
              rows={fact.value_type === "list" ? 4 : 2} aria-label={L("Value", "القيمة")} />
            {fact.value_type === "list" && <span className="s12 t3">{L("One item per line.", "عنصر واحد في كل سطر.")}</span>}
            <div className="row g8">
              <button type="button" className="btn pri sm" disabled={!payload || busy} onClick={save}>{L("Save and approve", "احفظ واعتمد")}</button>
              <button type="button" className="btn ghost sm" onClick={() => setEditing(false)}>{L("Cancel", "إلغاء")}</button>
            </div>
          </div>
        ) : <ValueView fact={fact} />}

        <details className="fact-src">
          <summary>
            <Icon name="link" size={12} /> <span dir="ltr">{sourceLabel(fact, L)}</span>
          </summary>
          <blockquote dir="auto">{fact.quote}</blockquote>
          <a className="s12" href={fact.source_url} target="_blank" rel="noreferrer">{L("Open the source", "افتح المصدر")} <Icon name="external" size={11} /></a>
        </details>
      </div>

      {!editing && (
        <div className="fact-actions">
          {onKeep && fact.status !== "rejected" && (
            <button type="button" className="btn pri sm" disabled={busy} onClick={onKeep}>{L("Keep this one", "أبقِ هذا")}</button>
          )}
          {fact.status !== "approved" && !onKeep && (
            <button type="button" className="btn sm icon" disabled={busy} onClick={() => setStatus("approved")}
              aria-label={L("Approve", "اعتماد")} title={L("Approve", "اعتماد")}><Icon name="check" size={15} /></button>
          )}
          {fact.status !== "rejected" && (
            <button type="button" className="btn ghost sm icon" disabled={busy} onClick={() => setStatus("rejected")}
              aria-label={L("Reject", "رفض")} title={L("Reject", "رفض")}><Icon name="x" size={15} /></button>
          )}
          <button type="button" className="btn ghost sm icon" disabled={busy} onClick={startEdit}
            aria-label={L("Edit", "تعديل")} title={L("Edit", "تعديل")}><Icon name="pencil" size={14} /></button>
        </div>
      )}
    </div>
  );
}

interface ListProps {
  categories: FactCategory[];
  selected: ReadonlySet<string>;
  busy: boolean;
  onToggle: (id: string) => void;
  onToggleMany: (ids: string[], on: boolean) => void;
  act: Act;
}

function CategoryList({ categories, selected, busy, onToggle, onToggleMany, act }: ListProps) {
  const L = useL();
  const { lang } = useI18n();
  return (
    <>
      {categories.map((c) => {
        const ids = [...c.facts, ...c.conflicts.flatMap((g) => g.facts)].map((f) => f.id);
        const all = ids.length > 0 && ids.every((id) => selected.has(id));
        return (
          <section key={c.category} className="fact-cat card">
            <header className="row g10">
              <input type="checkbox" checked={all} onChange={() => onToggleMany(ids, !all)}
                aria-label={L(`Select all in ${c.label_en}`, `تحديد كل ${c.label_ar}`)} />
              <h3 className="grow" dir="auto">{lang === "ar" ? c.label_ar : c.label_en}</h3>
              <span className="s12 t3">{ids.length}</span>
            </header>
            {c.conflicts.map((g) => (
              <div key={g.conflict_group} className="fact-conflict">
                <div className="row g8 s13"><Icon name="warning" size={14} />
                  <b>{L("These sources disagree", "المصادر غير متفقة")}</b>
                  <span className="t2">— {keyLabel(g.facts[0], lang)}. {L("Keep the right one; the others are rejected.", "أبقِ الصحيح وسيُرفض الباقي.")}</span>
                </div>
                {g.facts.map((f) => (
                  <FactRow key={f.id} fact={f} selected={selected.has(f.id)} busy={busy} onSelect={() => onToggle(f.id)} act={act}
                    onKeep={() => { const { approve, reject } = keepOne(g.facts.map((x) => x.id), f.id);
                      void act(async () => { await edrak.bulkFacts(approve, "approve"); if (reject.length) await edrak.bulkFacts(reject, "reject"); }); }} />
                ))}
              </div>
            ))}
            {c.facts.map((f) => <FactRow key={f.id} fact={f} selected={selected.has(f.id)} busy={busy} onSelect={() => onToggle(f.id)} act={act} />)}
          </section>
        );
      })}
    </>
  );
}

/**
 * The facts the research agent found, by category. Each is a suggestion until a person approves it. Facts that disagree
 * are shown together, and the counts on the tabs stay known while one status is shown.
 */
export function ResearchFacts({ refreshKey, active }: { refreshKey: string; active: boolean }) {
  const L = useL();
  const { lang } = useI18n();
  const facts = useAsync(() => edrak.researchFacts(), [refreshKey, lang]);
  const [tab, setTab] = useState<Tab | null>(null);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const counts = useMemo(() => countByStatus(allFacts(facts.data)), [facts.data]);
  // Open on what needs a decision; fall back to everything when nothing is waiting.
  useEffect(() => {
    if (tab === null && facts.data) setTab(counts.suggested > 0 ? "suggested" : "all");
  }, [facts.data, counts.suggested, tab]);
  const shown = tab ?? "suggested";
  const categories = useMemo(() => filterGrouped(facts.data, shown), [facts.data, shown]);
  const clear = useMemo(() => clearFactIds(facts.data), [facts.data]);

  const act: Act = async (work) => {
    setBusy(true);
    setError(null);
    try {
      await work();
      setSelected(new Set());
      facts.reload();
      return true;
    } catch (e) {
      setError(researchErrorMessage(e, L));
      return false;
    } finally {
      setBusy(false);
    }
  };
  const bulk = (ids: string[], action: "approve" | "reject") => void act(() => edrak.bulkFacts(ids, action));
  const toggleMany = (ids: string[], on: boolean) => setSelected((cur) => { const next = new Set(cur); ids.forEach((id) => (on ? next.add(id) : next.delete(id))); return next; });

  if (!facts.data) return <div className="shim" style={{ height: 160, borderRadius: 16 }} aria-busy="true" />;
  if (counts.all === 0) {
    return (
      <div className="empty">
        {active ? L("Facts appear here when the research finishes.", "ستظهر الحقائق هنا عند انتهاء البحث.")
          : L("No facts yet. Run the research to find them.", "لا حقائق بعد. شغّل البحث لإيجادها.")}
      </div>
    );
  }

  return (
    <div className="research-facts stack gap12">
      <div className="row g12 wrap jb">
        <Segmented<Tab> label={L("Show", "عرض")} value={shown} onChange={(v) => { setTab(v); setSelected(new Set()); }}
          options={STATUS_FILTERS.map(([value, en, ar]) => ({ value, label: `${L(en, ar)} · ${counts[value]}` }))} />
        {clear.length > 0 && (
          <button type="button" className="btn sm" disabled={busy} onClick={() => bulk(clear, "approve")}
            title={L("Facts from your own website or PDFs that are clearly stated and not disputed", "حقائق من موقعك أو ملفاتك واضحة وغير متنازع عليها")}>
            <Icon name="checks" size={14} /> {L(`Approve ${clear.length} clear facts`, `اعتماد ${clear.length} حقيقة واضحة`)}
          </button>
        )}
      </div>

      {error && <div className="form-error" role="alert"><Icon name="warning" size={16} /><span className="grow">{error}</span></div>}

      {categories.length === 0 ? (
        <div className="empty">{shown === "suggested" ? L("Nothing left to review.", "لا شيء متبقٍ للمراجعة.") : L("No facts here.", "لا حقائق هنا.")}</div>
      ) : (
        <CategoryList categories={categories} selected={selected} busy={busy}
          onToggle={(id) => setSelected((cur) => toggleId(cur, id))} onToggleMany={toggleMany} act={act} />
      )}

      {selected.size > 0 && (
        <div className="bulkbar row g10" role="region" aria-label={L("Selected facts", "الحقائق المحددة")}>
          <b>{L(`${selected.size} selected`, `${selected.size} محددة`)}</b>
          <div className="grow" />
          <button type="button" className="btn pri sm" disabled={busy} onClick={() => bulk([...selected], "approve")}><Icon name="check" size={14} /> {L("Approve", "اعتماد")}</button>
          <button type="button" className="btn sm" disabled={busy} onClick={() => bulk([...selected], "reject")}><Icon name="x" size={14} /> {L("Reject", "رفض")}</button>
          <button type="button" className="btn ghost sm" onClick={() => setSelected(new Set())}>{L("Clear", "مسح")}</button>
        </div>
      )}
    </div>
  );
}
