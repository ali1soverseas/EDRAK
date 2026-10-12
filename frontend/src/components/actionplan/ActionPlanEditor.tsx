import { useCallback, useEffect, useRef, useState } from "react";
import { useL } from "../../lib/useL";
import { HttpError, edrak } from "../../services/edrak";
import type { ActionPlanDoc, ActionPlanRow, BriefOption, BriefV3, Level, PlanAction } from "../../types/brief3";
import { Icon } from "../ui/Icon";

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T;
const LEVELS: Level[] = ["low", "moderate", "high"];
const allActions = (d: ActionPlanDoc) => d.workstreams.flatMap((w) => w.actions);

function Gantt({ doc }: { doc: ActionPlanDoc }) {
  const L = useL();
  const acts = allActions(doc);
  const weeks = Math.max(doc.total_weeks, ...acts.map((a) => a.start_week + a.duration_weeks - 1), 4);
  const step = weeks > 24 ? 4 : weeks > 12 ? 2 : 1;
  return (
    <div className="gantt" style={{ ["--weeks" as string]: weeks }}>
      <div className="gantt-head"><span />{Array.from({ length: Math.ceil(weeks / step) }, (_, i) => <span key={i} style={{ gridColumn: `span ${step}` }}>{L("W", "أ")}{i * step + 1}</span>)}</div>
      {doc.workstreams.map((w) => (
        <div key={w.id} className="gantt-group">
          <div className="gantt-ws">{w.name}</div>
          {w.actions.map((a) => (
            <div key={a.id} className="gantt-row">
              <span className="s12 ellip" title={a.title}>{a.id} · {a.title}</span>
              <div className="gantt-track">
                <i style={{ insetInlineStart: `${((a.start_week - 1) / weeks) * 100}%`, width: `${(a.duration_weeks / weeks) * 100}%` }} title={`${L("Week", "الأسبوع")} ${a.start_week}–${a.start_week + a.duration_weeks - 1}${a.depends_on.length ? ` · after ${a.depends_on.join(", ")}` : ""}`} />
              </div>
            </div>
          ))}
        </div>
      ))}
      <div className="gantt-row"><span className="s12 muted">{L("Gates", "البوابات")}</span><div className="gantt-track">{doc.milestones.map((m) => <em key={m.id} title={`${m.title}`} style={{ insetInlineStart: `${((m.week - 0.5) / weeks) * 100}%` }}>◆</em>)}</div></div>
    </div>
  );
}

function Text({ value, onChange, rows, ro, placeholder }: { value: string; onChange: (v: string) => void; rows?: number; ro: boolean; placeholder?: string }) {
  if (ro) return <span className="s13" dir="auto">{value || "—"}</span>;
  return rows ? <textarea className="textarea" rows={rows} value={value} onChange={(e) => onChange(e.target.value)} dir="auto" placeholder={placeholder} />
    : <input className="input" value={value} onChange={(e) => onChange(e.target.value)} dir="auto" placeholder={placeholder} />;
}

export function ActionPlanEditor({ brief, option, onDecisionChanged }: { brief: BriefV3; option: BriefOption; onDecisionChanged: () => void }) {
  const L = useL();
  const [row, setRow] = useState<ActionPlanRow | null>(null);
  const [doc, setDoc] = useState<ActionPlanDoc | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const poll = useRef<number | undefined>(undefined);

  const load = useCallback((r: ActionPlanRow) => {
    setRow(r);
    if (r.status !== "generating" && r.status !== "failed") { setDoc(clone(r.document as ActionPlanDoc)); setDirty(false); }
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

  useEffect(() => {
    setRow(null); setDoc(null);
    void edrak.actionPlans(brief.brief_id).then((all) => {
      const mine = all.find((p) => p.option_key === option.key);
      if (mine) { load(mine); if (mine.status === "generating") watch(mine.id); }
    }).catch(() => undefined);
    return () => window.clearTimeout(poll.current);
  }, [brief.brief_id, option.key, load, watch]);

  const create = async (n?: string) => {
    setBusy(true); setErrors([]);
    try {
      const r = row && row.status !== "approved" && row.status !== "failed"
        ? await edrak.regenerateActionPlan(row.id, option.key, n) : await edrak.createActionPlan(brief.brief_id, option.key, n);
      setNote("");
      watch(r.plan_id);
    } catch (e) {
      setErrors([(e as Error).message]);
    } finally { setBusy(false); }
  };

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
      if (dirty) current = await edrak.saveActionPlan(row.id, doc);
      await edrak.approveActionPlan(current.id);
      load(await edrak.actionPlan(current.id));
      onDecisionChanged();
    } catch (e) { setErrors(detail(e)); } finally { setBusy(false); }
  };

  const ro = row?.status === "approved";
  const edit = (fn: (d: ActionPlanDoc) => void) => {
    setDoc((d) => { if (!d) return d; const n = clone(d); fn(n); return n; });
    setDirty(true);
  };
  const setAct = (id: string, p: Partial<PlanAction>) => edit((d) => { const a = allActions(d).find((x) => x.id === id); if (a) Object.assign(a, p); });

  if (!row) {
    return (
      <div className="panel stack gap8">
        <b>{L("Action plan for option", "خطة العمل للخيار")} {option.key}</b>
        <span className="s13 muted">{L("Edrak drafts a sequenced plan with owners, gates, KPIs and a risk register. You edit it, then approve it. Approving records your decision.", "يكتب إدراك خطة متسلسلة بمسؤولين وبوابات ومؤشرات وسجل مخاطر. تعدّلها ثم تعتمدها، والاعتماد يسجّل قرارك.")}</span>
        <div><button type="button" className="btn blue" disabled={busy} onClick={() => void create()}>{L("Draft the action plan", "اكتب خطة العمل")}</button></div>
        {errors.map((e) => <div key={e} className="chip c-bad" style={{ whiteSpace: "normal" }}>{e}</div>)}
      </div>
    );
  }
  if (row.status === "generating") return <div className="panel"><div className="shim" style={{ height: 14, width: 260 }} /><p className="s13 muted">{L("Drafting the action plan from the brief…", "جارٍ كتابة خطة العمل من الموجز…")}</p></div>;
  if (row.status === "failed") return <div className="panel stack gap8"><div className="chip c-bad" style={{ whiteSpace: "normal" }}>{row.error}</div><div><button type="button" className="btn" onClick={() => void create()}>{L("Try again", "حاول مجددًا")}</button></div></div>;
  if (!doc) return null;

  const issues = row.issues;
  return (
    <section className="plan stack gap16" aria-label={L("Action plan", "خطة العمل")}>
      <header className="row g8 wrap">
        <div className="stack grow"><span className="eyebrow">{L("Action plan", "خطة العمل")} · {L("Option", "الخيار")} {option.key} · v{row.version}</span>
          <h3 style={{ margin: 0 }}>{doc.objective}</h3></div>
        <span className={`chip ${ro ? "c-dec" : "c-draft"}`}>{ro ? L("Approved", "معتمدة") : L("Draft: not approved", "مسودة: غير معتمدة")}</span>
      </header>

      <div className="grid2">
        <div className="panel stack gap4"><span className="eyebrow">{L("Objective", "الهدف")}</span><Text ro={ro} value={doc.objective} onChange={(v) => edit((d) => { d.objective = v; })} rows={2} />
          <span className="eyebrow">{L("Why this option", "لماذا هذا الخيار")}</span><Text ro={ro} value={doc.chosen_option.why} onChange={(v) => edit((d) => { d.chosen_option.why = v; })} rows={3} />
          {doc.chosen_option.evidence_refs.length > 0 && <div className="row g4 wrap">{doc.chosen_option.evidence_refs.map((n) => <span key={n} className="ev">{`E${n}`}</span>)}</div>}</div>
        <div className="panel stack gap8"><span className="eyebrow">{L("Not chosen", "لم يُختر")}</span>
          {doc.alternatives_not_chosen.map((a, i) => <div key={a.key} className="stack gap4"><b className="s13">{a.key}. {a.title}</b><Text ro={ro} value={a.why_not} rows={2} onChange={(v) => edit((d) => { d.alternatives_not_chosen[i].why_not = v; })} /></div>)}
          {doc.alternatives_not_chosen.length === 0 && <span className="s13 muted">—</span>}</div>
      </div>

      <div className="panel stack gap8">
        <div className="row g8"><span className="eyebrow grow">{L("Timeline", "الجدول الزمني")} · {doc.total_weeks} {L("weeks", "أسبوعًا")}</span>{doc.review_date && <span className="s12 muted">{L("Review on", "المراجعة في")} {doc.review_date}</span>}</div>
        <Gantt doc={doc} />
      </div>

      {doc.workstreams.map((w, wi) => (
        <div key={w.id} className="panel stack gap8">
          <div className="row g8"><span className="chip c-blue">{w.id}</span>{ro ? <b>{w.name}</b> : <input className="input grow" value={w.name} onChange={(e) => edit((d) => { d.workstreams[wi].name = e.target.value; })} />}</div>
          <div style={{ overflow: "auto" }}>
            <table className="tbl compact plan-tbl">
              <thead><tr><th>#</th><th>{L("Action", "الإجراء")}</th><th>{L("Owner", "المسؤول")}</th><th>{L("Start", "البداية")}</th><th>{L("Weeks", "المدة")}</th><th>{L("After", "بعد")}</th><th>{L("Deliverable", "المخرج")}</th><th>{L("Success measure", "مقياس النجاح")}</th>{!ro && <th />}</tr></thead>
              <tbody>{w.actions.map((a) => (
                <tr key={a.id}>
                  <td className="mono">{a.id}</td>
                  <td style={{ minWidth: 200 }}><Text ro={ro} value={a.title} onChange={(v) => setAct(a.id, { title: v })} /><Text ro={ro} value={a.description} rows={ro ? undefined : 2} onChange={(v) => setAct(a.id, { description: v })} /></td>
                  <td><Text ro={ro} value={a.owner_role} onChange={(v) => setAct(a.id, { owner_role: v })} /></td>
                  <td style={{ width: 70 }}>{ro ? a.start_week : <input className="input" type="number" min={1} value={a.start_week} onChange={(e) => setAct(a.id, { start_week: Math.max(1, +e.target.value || 1) })} />}</td>
                  <td style={{ width: 70 }}>{ro ? a.duration_weeks : <input className="input" type="number" min={1} value={a.duration_weeks} onChange={(e) => setAct(a.id, { duration_weeks: Math.max(1, +e.target.value || 1) })} />}</td>
                  <td style={{ minWidth: 90 }}>{ro ? a.depends_on.join(", ") || "—" : <input className="input" value={a.depends_on.join(", ")} onChange={(e) => setAct(a.id, { depends_on: e.target.value.split(/[ ,]+/).filter(Boolean) })} placeholder="A1, A2" />}</td>
                  <td><Text ro={ro} value={a.deliverable} onChange={(v) => setAct(a.id, { deliverable: v })} /></td>
                  <td><Text ro={ro} value={a.success_measure} onChange={(v) => setAct(a.id, { success_measure: v })} /></td>
                  {!ro && <td><button type="button" className="iconbtn" aria-label={L("Remove action", "احذف الإجراء")} onClick={() => edit((d) => { d.workstreams[wi].actions = d.workstreams[wi].actions.filter((x) => x.id !== a.id); allActions(d).forEach((x) => { x.depends_on = x.depends_on.filter((y) => y !== a.id); }); })}><Icon name="x" size={14} /></button></td>}
                </tr>))}</tbody>
            </table>
          </div>
          {!ro && <div><button type="button" className="btn ghost sm" onClick={() => edit((d) => { const n = Math.max(0, ...allActions(d).map((x) => +x.id.slice(1) || 0)) + 1; d.workstreams[wi].actions.push({ id: `A${n}`, title: L("New action", "إجراء جديد"), description: "", owner_role: "", start_week: 1, duration_weeks: 1, depends_on: [], deliverable: "", success_measure: "" }); })}><Icon name="plus" size={14} /> {L("Add action", "أضف إجراء")}</button></div>}
        </div>
      ))}

      <div className="grid2">
        <div className="panel stack gap8"><span className="eyebrow">{L("Milestones and decision gates", "المراحل وبوابات القرار")}</span>
          {doc.milestones.map((m, i) => (
            <div key={m.id} className="stack gap4 gate"><div className="row g8"><span className="chip c-line">{L("Week", "أسبوع")} {ro ? m.week : <input className="input" style={{ width: 64 }} type="number" min={1} value={m.week} onChange={(e) => edit((d) => { d.milestones[i].week = Math.max(1, +e.target.value || 1); })} />}</span><Text ro={ro} value={m.title} onChange={(v) => edit((d) => { d.milestones[i].title = v; })} /></div>
              <span className="s12 muted">{L("Go if", "نمضي إذا")}</span><Text ro={ro} value={m.go_criteria} rows={2} onChange={(v) => edit((d) => { d.milestones[i].go_criteria = v; })} />
              <span className="s12 muted">{L("Stop if", "نتوقف إذا")}</span><Text ro={ro} value={m.kill_criteria} rows={2} onChange={(v) => edit((d) => { d.milestones[i].kill_criteria = v; })} /></div>))}
        </div>
        <div className="panel stack gap8"><span className="eyebrow">{L("KPIs", "مؤشرات الأداء")}</span>
          <table className="tbl compact"><thead><tr><th>{L("Measure", "المقياس")}</th><th>{L("Baseline", "خط الأساس")}</th><th>{L("Target", "الهدف")}</th><th>{L("Cadence", "الوتيرة")}</th></tr></thead>
            <tbody>{doc.kpis.map((k, i) => (
              <tr key={k.id}><td className="s13">{k.name}{k.entity ? ` · ${k.entity}` : ""}</td>
                <td>{k.baseline ? <b title={L("Read from the stored metric", "مقروء من المقياس المخزن")}>{k.baseline}</b> : <span className="chip c-line">{L("new measure", "مقياس جديد")}</span>}</td>
                <td><div className="row g4">{k.direction === "increase" ? "▲" : k.direction === "decrease" ? "▼" : "■"}<Text ro={ro} value={k.target} onChange={(v) => edit((d) => { d.kpis[i].target = v; })} /></div><span className="s12 muted">{k.target_status === "proposed" ? L("proposed", "مقترح") : L("edited", "معدّل")}</span></td>
                <td><Text ro={ro} value={k.cadence} onChange={(v) => edit((d) => { d.kpis[i].cadence = v; })} /></td></tr>))}</tbody></table>
        </div>
      </div>

      <div className="grid2">
        <div className="panel stack gap8"><span className="eyebrow">{L("Risk register", "سجل المخاطر")}</span>
          {doc.risks.map((r, i) => (
            <div key={r.id} className="stack gap4 gate"><div className="row g8 wrap"><span className="chip c-bad">{r.id}</span>{(["likelihood", "impact"] as const).map((f) => ro ? <span key={f} className="chip c-line">{f} {r[f]}</span> : <select key={f} className="input" style={{ width: 120 }} value={r[f]} onChange={(e) => edit((d) => { d.risks[i][f] = e.target.value as Level; })}>{LEVELS.map((l) => <option key={l}>{l}</option>)}</select>)}</div>
              <Text ro={ro} value={r.text} rows={2} onChange={(v) => edit((d) => { d.risks[i].text = v; })} />
              <span className="s12 muted">{L("Early warning", "إنذار مبكر")}</span><Text ro={ro} value={r.early_warning} onChange={(v) => edit((d) => { d.risks[i].early_warning = v; })} />
              <span className="s12 muted">{L("Mitigation", "التخفيف")} {r.mitigation_action_ids.length > 0 && `(${r.mitigation_action_ids.join(", ")})`}</span><Text ro={ro} value={r.mitigation} rows={2} onChange={(v) => edit((d) => { d.risks[i].mitigation = v; })} />
              {r.evidence_refs.length > 0 && <div className="row g4">{r.evidence_refs.slice(0, 4).map((n) => <span key={n} className="ev">{`E${n}`}</span>)}</div>}</div>))}
        </div>
        <div className="stack gap16">
          <div className="panel stack gap8"><span className="eyebrow">{L("Opportunities to capture", "فرص للاستثمار")}</span>
            {doc.opportunities.map((o, i) => <div key={o.id} className="stack gap4 gate"><Text ro={ro} value={o.text} onChange={(v) => edit((d) => { d.opportunities[i].text = v; })} /><Text ro={ro} value={o.how_to_capture} rows={2} onChange={(v) => edit((d) => { d.opportunities[i].how_to_capture = v; })} />{o.action_ids.length > 0 && <span className="s12 muted">{L("Carried by", "ينفذها")} {o.action_ids.join(", ")}</span>}</div>)}</div>
          <div className="panel stack gap8"><span className="eyebrow">{L("Assumptions and open questions", "الافتراضات والأسئلة المفتوحة")}</span>
            {doc.assumptions.map((a, i) => <Text key={a.id} ro={ro} value={a.text} onChange={(v) => edit((d) => { d.assumptions[i].text = v; })} />)}
            {doc.open_questions.map((q, i) => <div key={q.id} className="row g4"><span className="chip c-ins">?</span><Text ro={ro} value={q.text} onChange={(v) => edit((d) => { d.open_questions[i].text = v; })} /></div>)}</div>
          <div className="panel stack gap4"><span className="eyebrow">{L("Resources", "الموارد")}</span>
            <Text ro={ro} value={doc.resources.join("\n")} rows={3} onChange={(v) => edit((d) => { d.resources = v.split("\n").filter((x) => x.trim()); })} /></div>
        </div>
      </div>

      {(errors.length > 0 || (issues && (issues.errors.length > 0 || issues.warnings.length > 0))) && (
        <div className="stack gap4">
          {[...errors, ...(issues?.errors ?? [])].map((e) => <div key={e} className="chip c-bad" style={{ whiteSpace: "normal" }}>{e}</div>)}
          {!ro && (issues?.warnings ?? []).map((w) => <div key={w} className="chip c-ins" style={{ whiteSpace: "normal" }}>{w}</div>)}
        </div>
      )}

      {!ro ? (
        <footer className="plan-actions row g8 wrap">
          <input className="input grow" style={{ minWidth: 220 }} value={note} onChange={(e) => setNote(e.target.value)} placeholder={L("Ask for changes, e.g. “add a legal review before launch”", "اطلب تعديلًا، مثل «أضف مراجعة قانونية قبل الإطلاق»")} dir="auto" />
          <button type="button" className="btn" disabled={busy || !note.trim()} onClick={() => void create(note)}>{L("Regenerate with this note", "أعد الكتابة بهذه الملاحظة")}</button>
          <button type="button" className="btn" disabled={busy || !dirty} onClick={() => void save()}>{L("Save changes", "احفظ التعديلات")}</button>
          <button type="button" className="btn blue" disabled={busy} onClick={() => void approve()}><Icon name="check" size={14} /> {L("Approve plan and record decision", "اعتمد الخطة وسجّل القرار")}</button>
        </footer>
      ) : (
        <p className="s13 muted"><Icon name="lock" size={12} /> {L("Approved and locked. Undo the decision from the toolbar to change it.", "معتمدة ومقفلة. تراجع عن القرار من شريط الأدوات لتعديلها.")}</p>
      )}
      {row.versions && row.versions.length > 1 && <p className="s12 muted">{L("Versions", "النسخ")}: {row.versions.map((v) => `v${v.version} (${v.status})`).join(" · ")}</p>}
    </section>
  );
}
