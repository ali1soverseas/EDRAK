import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useL } from "../../lib/useL";
import { HttpError, edrak } from "../../services/edrak";
import type { ActionPlanDoc, ActionPlanRow, BriefOption, Level, PlanAction, PlanOpportunity, PlanRisk } from "../../types/brief3";
import { Icon, type IconName } from "../ui/Icon";
import { EvChips } from "./InsightCard";
import { useDeck } from "./context";

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T;
const LEVELS: Level[] = ["low", "moderate", "high"];
const allActions = (d: ActionPlanDoc) => d.workstreams.flatMap((w) => w.actions);
const nextId = (prefix: string, ids: string[]) => `${prefix}${Math.max(0, ...ids.map((i) => +i.slice(1) || 0)) + 1}`;

function Gantt({ doc }: { doc: ActionPlanDoc }) {
  const L = useL();
  const acts = allActions(doc);
  const weeks = Math.max(doc.total_weeks, ...acts.map((a) => a.start_week + a.duration_weeks - 1), 4);
  const step = weeks > 24 ? 4 : weeks > 12 ? 2 : 1;
  return (
    <div className="gantt" style={{ ["--weeks" as string]: weeks }}>
      <div className="gantt-head"><span />{Array.from({ length: Math.ceil(weeks / step) }, (_, i) => <span key={i} style={{ gridColumn: `span ${step}` }}>{L("W", "أ")}{i * step + 1}</span>)}</div>
      {doc.workstreams.map((w) => (
        <div key={w.id}>
          <div className="gantt-ws">{w.name}</div>
          {w.actions.map((a) => (
            <div key={a.id} className="gantt-row">
              <span className="s12 ellip" title={a.title}>{a.id} · {a.title}</span>
              <div className="gantt-track"><i style={{ insetInlineStart: `${((a.start_week - 1) / weeks) * 100}%`, width: `${(a.duration_weeks / weeks) * 100}%` }} /></div>
            </div>
          ))}
        </div>
      ))}
      <div className="gantt-row"><span className="s12 t3">{L("Gates", "البوابات")}</span><div className="gantt-track">{doc.milestones.map((m) => <em key={m.id} title={m.title} style={{ insetInlineStart: `${((m.week - 0.5) / weeks) * 100}%` }}>◆</em>)}</div></div>
    </div>
  );
}

/** A text that is plain when locked and an input when editable. */
function Field({ value, onChange, ro, area, placeholder, label, className = "" }: {
  value: string; onChange: (v: string) => void; ro: boolean; area?: boolean; placeholder?: string; label: string; className?: string;
}) {
  if (ro) return value ? <span className={`s13 lh14 ${className}`} dir="auto">{value}</span> : null;
  return area
    ? <textarea className={`inp area ${className}`} rows={2} value={value} aria-label={label} placeholder={placeholder} dir="auto" onChange={(e) => onChange(e.target.value)} />
    : <input className={`inp ${className}`} value={value} aria-label={label} placeholder={placeholder} dir="auto" onChange={(e) => onChange(e.target.value)} />;
}

function Edited({ origin }: { origin?: string }) {
  const L = useL();
  return origin === "user" ? <span className="chip c-blue" title={L("Changed or added by a person", "غيّره أو أضافه شخص")}><Icon name="pencil" size={11} /> {L("Edited by you", "عدّلته أنت")}</span> : null;
}

function Group({ title, count, children, action }: { title: string; count: number; children: ReactNode; action?: ReactNode }) {
  return (
    <section className="plan-group">
      <div className="row g8"><span className="eyebrow grow">{title} <span className="t3">· {count}</span></span>{action}</div>
      {children}
    </section>
  );
}

/**
 * The action plan for the chosen option: ordered recommendations (owner and validation step each),
 * its risks and its opportunities, each citing the signals and sources behind it. States: draft,
 * edited, approved. Approving locks that version and records the decision; changing an approved
 * plan makes a new version and keeps the old one in the history.
 */
export function PlanPanel({ option, orderedOnly, onDecisionChanged }: { option: BriefOption; orderedOnly: boolean; onDecisionChanged: () => void }) {
  const L = useL();
  const { brief } = useDeck();
  const [row, setRow] = useState<ActionPlanRow | null>(null);
  const [doc, setDoc] = useState<ActionPlanDoc | null>(null);
  const [dirty, setDirty] = useState(false);
  const [editingApproved, setEditingApproved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [loaded, setLoaded] = useState(false);
  const poll = useRef<number | undefined>(undefined);

  const load = useCallback((r: ActionPlanRow) => {
    setRow(r);
    if (r.status !== "generating" && r.status !== "failed") { setDoc(clone(r.document as ActionPlanDoc)); setDirty(false); setEditingApproved(false); }
  }, []);

  const watch = useCallback((id: string) => {
    window.clearTimeout(poll.current);
    const tick = async () => {
      try {
        const r = await edrak.actionPlan(id);
        load(r);
        if (r.status === "generating") poll.current = window.setTimeout(tick, 1500);
      } catch { /* keep the last state */ }
    };
    void tick();
  }, [load]);

  const create = useCallback(async (n?: string, current?: ActionPlanRow | null) => {
    setBusy(true); setErrors([]);
    try {
      const r = current && current.status !== "approved" && current.status !== "failed"
        ? await edrak.regenerateActionPlan(current.id, option.key, n) : await edrak.createActionPlan(brief.brief_id, option.key, n);
      setNote("");
      watch(r.plan_id);
    } catch (e) {
      setErrors([(e as Error).message]);
    } finally { setBusy(false); }
  }, [brief.brief_id, option.key, watch]);

  // Choosing an option generates its plan; an existing plan is shown instead.
  useEffect(() => {
    setRow(null); setDoc(null); setLoaded(false);
    void edrak.actionPlans(brief.brief_id).then((all) => {
      const mine = all.find((p) => p.option_key === option.key);
      setLoaded(true);
      if (mine) { load(mine); if (mine.status === "generating") watch(mine.id); }
      else if (brief.lifecycle !== "decided") void create();
    }).catch(() => setLoaded(true));
    return () => window.clearTimeout(poll.current);
  }, [brief.brief_id, brief.lifecycle, option.key, load, watch, create]);

  const detail = (e: unknown): string[] => {
    const d = e instanceof HttpError ? (e.detail as { errors?: string[]; message?: string } | string) : null;
    return typeof d === "object" && d ? d.errors ?? [d.message ?? String(e)] : [(e as Error).message];
  };
  const save = async () => {
    if (!row || !doc) return;
    setBusy(true); setErrors([]);
    try { load(await edrak.saveActionPlan(row.id, doc)); } catch (e) { setErrors(detail(e)); } finally { setBusy(false); }
  };
  const approve = async () => {
    if (!row || !doc) return;
    setBusy(true); setErrors([]);
    try {
      let current = row;
      if (dirty || editingApproved) current = await edrak.saveActionPlan(row.id, doc);
      await edrak.approveActionPlan(current.id);
      load(await edrak.actionPlan(current.id));
      onDecisionChanged();
    } catch (e) { setErrors(detail(e)); } finally { setBusy(false); }
  };

  const ro = row?.status === "approved" && !editingApproved;
  const edit = (fn: (d: ActionPlanDoc) => void) => {
    setDoc((d) => { if (!d) return d; const n = clone(d); fn(n); return n; });
    setDirty(true);
  };
  const setAct = (id: string, p: Partial<PlanAction>) => edit((d) => { const a = allActions(d).find((x) => x.id === id); if (a) Object.assign(a, p); });
  const move = (wi: number, ai: number, by: number) => edit((d) => {
    const list = d.workstreams[wi].actions; const j = ai + by;
    if (j < 0 || j >= list.length) return;
    [list[ai], list[j]] = [list[j], list[ai]];
  });
  const setRisk = (i: number, p: Partial<PlanRisk>) => edit((d) => { Object.assign(d.risks[i], p); });
  const setOpp = (i: number, p: Partial<PlanOpportunity>) => edit((d) => { Object.assign(d.opportunities[i], p); });

  if (!loaded || (!row && busy)) return <div className="plan-shell"><div className="shim" style={{ height: 14, width: 260 }} /><p className="s13 t3">{L("Drafting the action plan for this option…", "جارٍ كتابة خطة العمل لهذا الخيار…")}</p></div>;
  if (!row) {
    return (
      <div className="plan-shell stack gap8">
        <b>{L("Action plan", "خطة العمل")} · {L("Option", "الخيار")} {option.key}</b>
        {errors.map((e) => <div key={e} className="chip c-bad wrap-chip">{e}</div>)}
        <div><button type="button" className="btn pri" disabled={busy} onClick={() => void create()}>{L("Draft the action plan", "اكتب خطة العمل")}</button></div>
      </div>
    );
  }
  if (row.status === "generating") return <div className="plan-shell"><div className="shim" style={{ height: 14, width: 260 }} /><p className="s13 t3">{L("Drafting the action plan from the brief…", "جارٍ كتابة خطة العمل من الموجز…")}</p></div>;
  if (row.status === "failed") return <div className="plan-shell stack gap8"><div className="chip c-bad wrap-chip">{row.error}</div><div><button type="button" className="btn" onClick={() => void create(undefined, row)}>{L("Try again", "حاول مجددًا")}</button></div></div>;
  if (!doc) return null;

  const state = editingApproved ? "edited" : row.state ?? row.status;
  const STATE: Record<string, [string, string, IconName]> = {
    draft: [L("Draft", "مسودة"), "c-draft", "file"], edited: [L("Edited, not approved", "معدّلة، غير معتمدة"), "c-blue", "pencil"],
    approved: [L("Approved and locked", "معتمدة ومقفلة"), "c-dec", "lock"],
  };
  const [stateLabel, stateCls, stateIcon] = STATE[state] ?? STATE.draft;
  let n = 0;

  return (
    <section className="plan-shell stack gap16" aria-label={L("Action plan", "خطة العمل")}>
      <header className="row g10 wrap">
        <div className="stack grow" style={{ minWidth: 0 }}>
          <span className="eyebrow">{L("Action plan", "خطة العمل")} · {L("Option", "الخيار")} {option.key} · v{row.version}</span>
          {ro ? <h3 className="plan-title" dir="auto">{doc.objective}</h3>
            : <Field ro={false} value={doc.objective} label={L("Objective", "الهدف")} className="plan-title-inp" onChange={(v) => edit((d) => { d.objective = v; })} />}
        </div>
        <span className={`chip ${stateCls}`}><Icon name={stateIcon} size={12} /> {stateLabel}</span>
      </header>

      {orderedOnly && <p className="s12 t3"><Icon name="info" size={12} /> {L("Impact, urgency and feasibility were not all assessed, so actions are an ordered list, not a priority matrix.", "لم يُقيَّم الأثر والإلحاح والجدوى كلها، لذا الإجراءات قائمة مرتبة لا مصفوفة أولويات.")}</p>}

      <Group title={L("Recommendations, in order", "التوصيات بالترتيب")} count={allActions(doc).length}>
        {doc.workstreams.map((w, wi) => (
          <div key={w.id} className="stack gap6">
            <div className="row g8 ws-name">{ro ? <b className="s13">{w.name}</b> : <Field ro={false} value={w.name} label={L("Workstream", "مسار العمل")} onChange={(v) => edit((d) => { d.workstreams[wi].name = v; })} />}</div>
            <ol className="rec-list">
              {w.actions.map((a, ai) => {
                n += 1;
                return (
                  <li key={a.id} className={`rec ${a.origin === "user" ? "user" : ""}`}>
                    <span className="rec-no mono">{String(n).padStart(2, "0")}</span>
                    <div className="stack gap6 grow" style={{ minWidth: 0 }}>
                      <div className="row g8 wrap">
                        {ro ? <b className="s14 lh12 grow" dir="auto">{a.title}</b> : <Field ro={false} value={a.title} label={L("Action", "الإجراء")} className="grow" onChange={(v) => setAct(a.id, { title: v })} />}
                        <Edited origin={a.origin} />
                      </div>
                      <div className="rec-grid">
                        <label className="rec-field"><span className="t3 s12">{L("Owner", "المسؤول")}</span>
                          {ro ? <span className="s13">{a.owner_role || "—"}</span> : <Field ro={false} value={a.owner_role} label={L("Owner", "المسؤول")} placeholder={L("A role, e.g. Head of pricing", "دور، مثل رئيس التسعير")} onChange={(v) => setAct(a.id, { owner_role: v })} />}</label>
                        <label className="rec-field"><span className="t3 s12">{L("Validation step", "خطوة التحقق")}</span>
                          {ro ? <span className="s13 lh14">{a.validation || a.success_measure || "—"}</span> : <Field ro={false} value={a.validation ?? ""} label={L("Validation step", "خطوة التحقق")} placeholder={L("How we check it worked", "كيف نتحقق أنه نجح")} onChange={(v) => setAct(a.id, { validation: v })} />}</label>
                      </div>
                      <div className="row g8 wrap s12 t3">
                        <span>{L("Week", "أسبوع")} {a.start_week}–{a.start_week + a.duration_weeks - 1}</span>
                        {a.depends_on.length > 0 && <span>· {L("after", "بعد")} {a.depends_on.join(", ")}</span>}
                        {(a.signal_ids?.length ?? 0) > 0 && <span>· {L(`${a.signal_ids!.length} signal(s)`, `${a.signal_ids!.length} إشارة`)}</span>}
                        <EvChips refs={a.evidence_refs ?? []} max={4} />
                      </div>
                    </div>
                    {!ro && (
                      <div className="rec-tools">
                        <button type="button" className="iconbtn sm" aria-label={L("Move up", "انقل لأعلى")} disabled={ai === 0} onClick={() => move(wi, ai, -1)}><Icon name="chevronUp" size={13} /></button>
                        <button type="button" className="iconbtn sm" aria-label={L("Move down", "انقل لأسفل")} disabled={ai === w.actions.length - 1} onClick={() => move(wi, ai, 1)}><Icon name="chevronDown" size={13} /></button>
                        <button type="button" className="iconbtn sm" aria-label={L("Remove", "احذف")} onClick={() => edit((d) => { d.workstreams[wi].actions = d.workstreams[wi].actions.filter((x) => x.id !== a.id); allActions(d).forEach((x) => { x.depends_on = x.depends_on.filter((y) => y !== a.id); }); })}><Icon name="trash" size={13} /></button>
                      </div>
                    )}
                  </li>
                );
              })}
            </ol>
            {!ro && <div><button type="button" className="btn ghost sm" onClick={() => edit((d) => { d.workstreams[wi].actions.push({ id: nextId("A", allActions(d).map((x) => x.id)), title: L("New action", "إجراء جديد"), description: "", owner_role: "", start_week: 1, duration_weeks: 1, depends_on: [], deliverable: "", success_measure: "", validation: "", origin: "user" }); })}><Icon name="plus" size={13} /> {L("Add a recommendation", "أضف توصية")}</button></div>}
          </div>
        ))}
      </Group>

      <div className="plan-two">
        <Group title={L("Risks", "المخاطر")} count={doc.risks.length}
          action={!ro ? <button type="button" className="btn ghost sm" onClick={() => edit((d) => { d.risks.push({ id: nextId("R", d.risks.map((x) => x.id)), text: L("New risk", "خطر جديد"), likelihood: "moderate", impact: "moderate", early_warning: "", mitigation: "", mitigation_action_ids: [], owner_role: "", evidence_refs: [], origin: "user" }); })}><Icon name="plus" size={13} /> {L("Add", "أضف")}</button> : undefined}>
          {doc.risks.map((r, i) => (
            <div key={r.id} className={`plan-item ${r.origin === "user" ? "user" : ""}`}>
              <div className="row g6 wrap">
                <Field ro={ro} area value={r.text} label={L("Risk", "الخطر")} className="grow" onChange={(v) => setRisk(i, { text: v })} />
                <Edited origin={r.origin} />
                {!ro && <button type="button" className="iconbtn sm" aria-label={L("Remove", "احذف")} onClick={() => edit((d) => { d.risks.splice(i, 1); })}><Icon name="trash" size={13} /></button>}
              </div>
              <div className="row g6 wrap s12">
                {(["likelihood", "impact"] as const).map((f) => ro ? <span key={f} className="chip c-line">{f === "likelihood" ? L("Likelihood", "الاحتمال") : L("Impact", "الأثر")} {r[f]}</span>
                  : <label key={f} className="row g4 t3">{f === "likelihood" ? L("Likelihood", "الاحتمال") : L("Impact", "الأثر")}<select className="inp sel" value={r[f]} onChange={(e) => setRisk(i, { [f]: e.target.value as Level })}>{LEVELS.map((l) => <option key={l} value={l}>{l}</option>)}</select></label>)}
              </div>
              <span className="t3 s12">{L("Mitigation", "التخفيف")}</span>
              <Field ro={ro} value={r.mitigation} label={L("Mitigation", "التخفيف")} onChange={(v) => setRisk(i, { mitigation: v })} />
              <div className="row g6 wrap s12 t3">{r.owner_role && <span>{L("Owner", "المسؤول")}: {r.owner_role}</span>}<EvChips refs={r.evidence_refs} max={4} /></div>
            </div>
          ))}
        </Group>
        <Group title={L("Opportunities", "الفرص")} count={doc.opportunities.length}
          action={!ro ? <button type="button" className="btn ghost sm" onClick={() => edit((d) => { d.opportunities.push({ id: nextId("O", d.opportunities.map((x) => x.id)), text: L("New opportunity", "فرصة جديدة"), how_to_capture: "", action_ids: [], evidence_refs: [], origin: "user" }); })}><Icon name="plus" size={13} /> {L("Add", "أضف")}</button> : undefined}>
          {doc.opportunities.map((o, i) => (
            <div key={o.id} className={`plan-item ${o.origin === "user" ? "user" : ""}`}>
              <div className="row g6 wrap">
                <Field ro={ro} area value={o.text} label={L("Opportunity", "الفرصة")} className="grow" onChange={(v) => setOpp(i, { text: v })} />
                <Edited origin={o.origin} />
                {!ro && <button type="button" className="iconbtn sm" aria-label={L("Remove", "احذف")} onClick={() => edit((d) => { d.opportunities.splice(i, 1); })}><Icon name="trash" size={13} /></button>}
              </div>
              <span className="t3 s12">{L("How to capture it", "كيف نغتنمها")}</span>
              <Field ro={ro} value={o.how_to_capture} label={L("How to capture it", "كيف نغتنمها")} onChange={(v) => setOpp(i, { how_to_capture: v })} />
              <div className="row g6 wrap s12 t3">{o.action_ids.length > 0 && <span>{L("Carried by", "ينفذها")} {o.action_ids.join(", ")}</span>}<EvChips refs={o.evidence_refs} max={4} /></div>
            </div>
          ))}
        </Group>
      </div>

      <details className="plan-more">
        <summary className="row g8"><Icon name="chevronDown" size={14} /> <span className="s13 b6">{L("Timeline, decision gates and KPIs", "الجدول وبوابات القرار والمؤشرات")}</span></summary>
        <div className="stack gap16" style={{ paddingTop: 12 }}>
          <Gantt doc={doc} />
          {doc.milestones.length > 0 && <div className="stack gap6">{doc.milestones.map((m) => <div key={m.id} className="row g8 s13"><span className="chip c-line">{L("Week", "أسبوع")} {m.week}</span><b dir="auto">{m.title}</b>{m.go_criteria && <span className="t3" dir="auto">· {L("go if", "نمضي إذا")} {m.go_criteria}</span>}</div>)}</div>}
          {doc.kpis.length > 0 && (
            <table className="tbl compact"><thead><tr><th>{L("Measure", "المقياس")}</th><th>{L("Baseline", "خط الأساس")}</th><th>{L("Target", "الهدف")}</th></tr></thead>
              <tbody>{doc.kpis.map((k, i) => <tr key={k.id}><td className="s13">{k.name}{k.entity ? ` · ${k.entity}` : ""}</td><td>{k.baseline ? <b>{k.baseline}</b> : <span className="chip c-line">{L("new measure", "مقياس جديد")}</span>}</td>
                <td><Field ro={ro} value={k.target} label={L("Target", "الهدف")} onChange={(v) => edit((d) => { d.kpis[i].target = v; })} /></td></tr>)}</tbody></table>
          )}
        </div>
      </details>

      {(errors.length > 0 || (row.issues?.errors.length ?? 0) > 0) && (
        <div className="stack gap4">{[...errors, ...(row.issues?.errors ?? [])].map((e) => <div key={e} className="chip c-bad wrap-chip">{e}</div>)}</div>
      )}

      {ro ? (
        <footer className="plan-foot row g8 wrap">
          <span className="s13 t2 grow"><Icon name="lock" size={12} /> {L("Approved and locked. The decision is in the decision log.", "معتمدة ومقفلة. القرار في سجل القرارات.")}</span>
          <button type="button" className="btn sm" onClick={() => { setEditingApproved(true); setDirty(false); }}><Icon name="pencil" size={13} /> {L(`Make changes (creates version ${row.version + 1})`, `عدّل (تنشئ النسخة ${row.version + 1})`)}</button>
        </footer>
      ) : (
        <footer className="plan-foot row g8 wrap">
          {!editingApproved && (
            <>
              <input className="inp grow" style={{ minWidth: 220 }} value={note} onChange={(e) => setNote(e.target.value)} dir="auto"
                placeholder={L("Ask for changes, e.g. “add a legal review before launch”", "اطلب تعديلًا، مثل «أضف مراجعة قانونية قبل الإطلاق»")} />
              <button type="button" className="btn" disabled={busy || !note.trim()} onClick={() => void create(note, row)}>{L("Redraft with this note", "أعد الكتابة بهذه الملاحظة")}</button>
            </>
          )}
          {editingApproved && <button type="button" className="btn ghost" onClick={() => load(row)}>{L("Cancel", "إلغاء")}</button>}
          <button type="button" className="btn" disabled={busy || !dirty} onClick={() => void save()}>{editingApproved ? L("Save as a new version", "احفظ كنسخة جديدة") : L("Save changes", "احفظ التعديلات")}</button>
          <button type="button" className="btn pri" disabled={busy} onClick={() => void approve()}><Icon name="check" size={14} /> {L("Approve and record decision", "اعتمد وسجّل القرار")}</button>
        </footer>
      )}
      {row.versions && row.versions.length > 1 && (
        <p className="s12 t3">{L("History", "السجل")}: {row.versions.map((v) => `v${v.version} ${v.state ?? v.status}`).join(" · ")}</p>
      )}
    </section>
  );
}
