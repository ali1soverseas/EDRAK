import { Fragment, useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { modKey } from "../../lib/platform";
import { useL } from "../../lib/useL";
import { edrak, type ChatLabel, type ChatMessageRow, type ChatSource, type ChatSourceKind, type ChatThreadRow, type NewThread } from "../../services/edrak";
import { Icon } from "../ui/Icon";
import { ThinkingMark } from "../ui/ThinkingMark";

export interface ChatScope { briefId?: string; cardKey?: string; signalId?: string; title?: string; nonce?: number }

interface Msg {
  id?: string;
  role: "user" | "assistant";
  content: string;
  label?: ChatLabel;
  kinds?: ChatSourceKind[];
  sources?: ChatSource[];
  notes?: string[];
  proposal?: string | null;
  followUps?: string[];
  rating?: "up" | "down" | null;
  addedNote?: string;
  error?: boolean;
  retry?: string;          // the question to send again after an error
  streaming?: boolean;
  stopped?: boolean;
}

type Kind = "card" | "signal" | "brief" | "workspace";

const TAG = String.raw`(?:E\d+|B\d+(?::[EN]\d+)?|C\d+|A\d+|P|CF\d+|D\d+|W\d+)`;
const INLINE = new RegExp(String.raw`(\[${TAG}\])|(\*\*[^*\n]+\*\*)|(\x60[^\x60\n]+\x60)|(https?:\/\/[^\s)\]]*[^\s)\].,;:!?،])`, "g");

/* ------------------------------------------------------------------------------------------ rendering */

/** One citation: E-numbers open the evidence drawer; other sources show their title and link out when they can. */
function Cite({ tag, sources, onEvidence }: { tag: string; sources?: ChatSource[]; onEvidence?: (n: number) => void }) {
  const src = sources?.find((s) => s.tag === tag);
  const tip = src ? `${src.title}${src.excerpt ? ` — ${src.excerpt}` : ""}` : tag;
  const e = tag.match(/^E(\d+)$/);
  if (e && onEvidence) return <button type="button" className="ev" title={tip} onClick={() => onEvidence(+e[1])}>{tag}</button>;
  const href = src?.url ?? (src?.brief_id && src.kind === "workspace" ? `/briefs/${src.brief_id}` : null);
  if (href) return <a className={`ev cite-${src?.kind ?? "x"}`} href={href} target={src?.url ? "_blank" : undefined} rel="noopener noreferrer" title={tip}>{tag}</a>;
  return <span className={`ev cite-${src?.kind ?? "x"}`} title={tip}>{tag}</span>;
}

function inline(text: string, sources: ChatSource[] | undefined, onEvidence?: (n: number) => void): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(INLINE)) {
    const at = m.index ?? 0;
    if (at > last) out.push(text.slice(last, at));
    if (m[1]) out.push(<Cite key={at} tag={m[1].slice(1, -1)} sources={sources} onEvidence={onEvidence} />);
    else if (m[2]) out.push(<b key={at}>{m[2].slice(2, -2)}</b>);
    else if (m[3]) out.push(<code key={at}>{m[3].slice(1, -1)}</code>);
    else if (m[4]) out.push(<a key={at} href={m[4]} target="_blank" rel="noopener noreferrer">{m[4].replace(/^https?:\/\//, "").slice(0, 48)}</a>);
    last = at + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** A small, safe Markdown subset: paragraphs, "- " and "1. " lists, "### " headings, **bold**, `code`, links and citations. */
function Markdown({ text, sources, onEvidence }: { text: string; sources?: ChatSource[]; onEvidence?: (n: number) => void }) {
  const blocks: ReactNode[] = [];
  const lines = text.replace(/\r/g, "").split("\n");
  let para: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  const flushPara = () => {
    if (para.length) blocks.push(<p key={blocks.length}>{para.map((l, i) => <Fragment key={i}>{i > 0 && <br />}{inline(l, sources, onEvidence)}</Fragment>)}</p>);
    para = [];
  };
  const flushList = () => {
    if (list) {
      const items = list.items.map((it, i) => <li key={i}>{inline(it, sources, onEvidence)}</li>);
      blocks.push(list.ordered ? <ol key={blocks.length}>{items}</ol> : <ul key={blocks.length}>{items}</ul>);
    }
    list = null;
  };
  for (const raw of lines) {
    const line = raw.trimEnd();
    const bullet = line.match(/^\s*[-*•]\s+(.*)$/);
    const num = line.match(/^\s*\d+[.)]\s+(.*)$/);
    const head = line.match(/^#{1,4}\s+(.*)$/);
    if (bullet || num) {
      flushPara();
      const ordered = !!num;
      if (!list || list.ordered !== ordered) { flushList(); list = { ordered, items: [] }; }
      list.items.push((bullet ?? num)![1]);
    } else if (head) {
      flushPara(); flushList();
      blocks.push(<b key={blocks.length} className="md-h">{inline(head[1], sources, onEvidence)}</b>);
    } else if (!line.trim()) {
      flushPara(); flushList();
    } else {
      flushList();
      para.push(line);
    }
  }
  flushPara(); flushList();
  return <div className="chat-md" dir="auto">{blocks}</div>;
}

function SourceLabel({ label, kinds, kind }: { label?: ChatLabel; kinds?: ChatSourceKind[]; kind: Kind }) {
  const L = useL();
  const chip = (cls: string, icon: Parameters<typeof Icon>[0]["name"], text: string) => (
    <span className={`chip ${cls}`}><Icon name={icon} size={11} /> {text}</span>
  );
  const main = (() => {
    switch (label) {
      case "brief": return chip("c-ok", "check", L("From the brief", "من الموجز"));
      case "workspace": return chip("c-ok", "layers", L("From your briefs", "من موجزاتك"));
      case "company": return chip("c-blue", "building", L("From your company profile", "من ملف شركتك"));
      case "documents": return chip("c-line", "fileLines", L("From your documents", "من مستنداتك"));
      case "web_unverified": return chip("c-ins", "globe", L("From the web, unverified", "من الويب، غير موثّق"));
      case "not_covered": return chip("c-draft", "info", kind === "workspace" ? L("Not covered by what Edrak holds", "غير مغطّى فيما يملكه إدراك") : L("Not covered by the brief", "غير مغطّى في الموجز"));
      case "declined": return chip("c-draft", "shield", L("Not answered", "لم تتم الإجابة"));
      default: return null;
    }
  })();
  const primaryKind: Partial<Record<ChatLabel, ChatSourceKind>> = { brief: "brief", workspace: "workspace", company: "company", documents: "documents", web_unverified: "web" };
  const extra = (kinds ?? []).filter((k) => k !== primaryKind[label as ChatLabel]);
  const kindName: Record<ChatSourceKind, string> = {
    brief: L("brief", "الموجز"), workspace: L("other briefs", "موجزات أخرى"), company: L("company profile", "ملف الشركة"),
    documents: L("documents", "المستندات"), web: L("web", "الويب"),
  };
  if (!main) return null;
  return (
    <span className="row g6 wrap">
      {main}
      {extra.length > 0 && <span className="s12 t3">{L("also", "وأيضًا")}: {extra.map((k) => kindName[k]).join(" · ")}</span>}
    </span>
  );
}

function Sources({ sources, onEvidence }: { sources: ChatSource[]; onEvidence?: (n: number) => void }) {
  const L = useL();
  const [open, setOpen] = useState(false);
  if (!sources.length) return null;
  return (
    <div className="chat-sources">
      <button type="button" className="linkbtn s12 t2" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <Icon name={open ? "chevronUp" : "chevronDown"} size={12} /> {L(`Sources (${sources.length})`, `المصادر (${sources.length})`)}
      </button>
      {open && (
        <ol className="chat-source-list">
          {sources.map((s) => (
            <li key={s.tag}>
              <Cite tag={s.tag} sources={sources} onEvidence={onEvidence} />
              <span className="s12 grow" dir="auto" style={{ minWidth: 0 }}>
                <b className="ellip" style={{ display: "block" }}>{s.title}</b>
                {s.excerpt && <span className="t3 lh14">{s.excerpt}</span>}
              </span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function Thumb({ down = false }: { down?: boolean }) {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"
      style={down ? { transform: "rotate(180deg)" } : undefined} aria-hidden="true">
      <path d="M7 10v11H4a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1h3Zm0 0 4-7a2 2 0 0 1 2.4 1.6L13 9h5.6a2 2 0 0 1 2 2.3l-1.2 7A2 2 0 0 1 17.4 20H7" />
    </svg>
  );
}

function fromRow(r: ChatMessageRow): Msg {
  return r.role === "user" ? { id: r.id, role: "user", content: r.content } : {
    id: r.id, role: "assistant", content: r.content, label: r.label ?? undefined, kinds: r.kinds, sources: r.sources,
    notes: r.notes, proposal: r.proposal, followUps: r.follow_ups, rating: r.rating,
  };
}

/* ------------------------------------------------------------------------------------------ the panel */

/**
 * Ask Edrak. Docked beside the brief (works in full screen) or floating over the app.
 * A conversation: follow-ups keep their context, answers stream in, each says where it comes from
 * (the brief, your other briefs, your company profile, your documents or the web), and past chats can be reopened.
 */
export function ChatPanel({ scope, variant, onClose, onEvidence }: {
  scope: ChatScope; variant: "docked" | "floating"; onClose: () => void; onEvidence?: (n: number) => void;
}) {
  const L = useL();
  const [thread, setThread] = useState<string | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [history, setHistory] = useState<ChatThreadRow[] | null>(null);
  const [copied, setCopied] = useState<number | null>(null);
  const list = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const abort = useRef<AbortController | null>(null);
  const kind: Kind = scope.cardKey ? "card" : scope.signalId ? "signal" : scope.briefId ? "brief" : "workspace";

  useEffect(() => { input.current?.focus(); }, []);
  useEffect(() => () => abort.current?.abort(), []);
  useEffect(() => { list.current?.scrollTo({ top: list.current.scrollHeight, behavior: "smooth" }); }, [msgs, busy]);
  useEffect(() => {
    const el = input.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [text]);

  const subject = scope.title ? `“${scope.title}”` : "";
  const suggestions: string[] =
    kind === "card" ? [
      L(`Where does ${subject || "this figure"} come from?`, `من أين يأتي ${subject || "هذا الرقم"}؟`),
      L("How confident should we be in this, and why?", "إلى أي حد نثق بهذا، ولماذا؟"),
      L("What would change this figure?", "ما الذي قد يغيّر هذا الرقم؟"),
      L("Is there newer public data on this?", "هل توجد بيانات عامة أحدث عن هذا؟"),
    ]
    : kind === "signal" ? [
      L(`Why does ${subject} matter for us?`, `لماذا تهمنا ${subject}؟`),
      L(`Which sources support ${subject}?`, `ما المصادر التي تدعم ${subject}؟`),
      L(`What would weaken ${subject}?`, `ما الذي قد يُضعف ${subject}؟`),
    ]
    : kind === "brief" ? [
      L(subject ? `Explain ${subject} in plain words` : "What is the single most important takeaway?", subject ? `اشرح ${subject} بكلمات بسيطة` : "ما أهم خلاصة واحدة؟"),
      L("Which figures rest on a single source?", "أي الأرقام تعتمد على مصدر واحد؟"),
      L("Where do the sources disagree?", "أين تتعارض المصادر؟"),
      L("What is missing before we decide?", "ما الناقص قبل أن نقرر؟"),
    ]
    : [
      L("Who are our target customers?", "من هم عملاؤنا المستهدفون؟"),
      L("What do we know about our competitors?", "ماذا نعرف عن منافسينا؟"),
      L("What changed with our competitors recently?", "ما الذي تغيّر لدى منافسينا مؤخرًا؟"),
      L("Summarise my latest brief", "لخّص أحدث موجز لي"),
    ];

  const stepLabel = (tool: string) => {
    switch (tool) {
      case "web_search": return L("Searching the web", "يبحث في الويب");
      case "get_metric": case "get_card": return L("Reading the figures", "يقرأ الأرقام");
      case "search_brief": return L("Searching the brief", "يبحث في الموجز");
      case "search_workspace": case "list_briefs": return L("Looking through your briefs", "يبحث في موجزاتك");
      case "search_company_facts": return L("Checking your company facts", "يراجع حقائق شركتك");
      case "search_documents": return L("Reading your documents", "يقرأ مستنداتك");
      case "get_watchlist": return L("Checking the watchlist", "يراجع قائمة المتابعة");
      case "recent_alerts": return L("Checking recent alerts", "يراجع التنبيهات الأخيرة");
      case "request_rerun": return L("Noting what to research", "يدوّن ما يحتاج بحثًا");
      default: return L("Thinking", "يفكر");
    }
  };

  const ensureThread = async (): Promise<string> => {
    if (thread) return thread;
    const body: NewThread = scope.briefId
      ? scope.cardKey ? { scope: "card", brief_id: scope.briefId, card_key: scope.cardKey }
        : scope.signalId ? { scope: "signal", brief_id: scope.briefId, signal_id: scope.signalId }
        : { scope: "brief", brief_id: scope.briefId }
      : { scope: "workspace" };
    const id = (await edrak.newThread(body)).thread_id;
    setThread(id);
    return id;
  };

  /** Replace the streaming bubble (or add one) with `m`. */
  const settle = (m: Msg) => setMsgs((all) => {
    const last = all[all.length - 1];
    return last?.role === "assistant" && last.streaming ? [...all.slice(0, -1), m] : [...all, m];
  });

  const send = async (raw: string, opts: { regenerate?: boolean } = {}) => {
    const content = raw.trim();
    if ((!content && !opts.regenerate) || busy) return;
    setText("");
    setHistory(null);
    if (opts.regenerate) {
      setMsgs((m) => (m[m.length - 1]?.role === "assistant" ? m.slice(0, -1) : m));
    } else {
      setMsgs((m) => [...m, { role: "user", content }]);
    }
    setBusy(kind === "workspace" ? L("Thinking", "يفكر") : L("Reading the brief", "يقرأ الموجز"));
    const ctrl = new AbortController();
    abort.current = ctrl;
    let answered = false;
    try {
      const id = await ensureThread();
      await edrak.streamAsk(id, content, (e) => {
        if (e.type === "step") {
          setBusy(stepLabel(String(e.data.tool ?? "")));
        } else if (e.type === "delta") {
          const piece = String(e.data.text ?? "");
          setMsgs((all) => {
            const last = all[all.length - 1];
            if (last?.role === "assistant" && last.streaming) return [...all.slice(0, -1), { ...last, content: last.content + piece }];
            return [...all, { role: "assistant", content: piece, streaming: true }];
          });
        } else if (e.type === "answer") {
          answered = true;
          const d = e.data;
          settle({
            id: d.message_id as string | undefined, role: "assistant", content: String(d.answer ?? ""), label: d.label as ChatLabel,
            kinds: (d.kinds ?? []) as ChatSourceKind[], sources: (d.sources ?? []) as ChatSource[], notes: (d.notes ?? []) as string[],
            proposal: (d.proposal ?? null) as string | null, followUps: (d.follow_ups ?? []) as string[], rating: null,
          });
        } else if (e.type === "error") {
          answered = true;
          settle({ role: "assistant", content: String(e.data.message ?? L("Something went wrong.", "حدث خطأ.")), error: true,
                   retry: e.data.code === "rate_limited" ? undefined : content || undefined });
        }
      }, { signal: ctrl.signal, regenerate: opts.regenerate });
      if (!answered) settle({ role: "assistant", content: L("No answer came back.", "لم يصل رد."), error: true, retry: content || undefined });
    } catch (e) {
      if (ctrl.signal.aborted) {
        setMsgs((all) => {
          const last = all[all.length - 1];
          if (last?.role === "assistant" && last.streaming) return [...all.slice(0, -1), { ...last, streaming: false, stopped: true }];
          return [...all, { role: "assistant", content: L("Stopped.", "توقّف."), stopped: true }];
        });
      } else {
        settle({ role: "assistant", content: (e as Error).message || L("Something went wrong.", "حدث خطأ."), error: true, retry: content || undefined });
      }
    } finally {
      abort.current = null;
      setBusy(null);
      input.current?.focus();
    }
  };

  const stop = () => abort.current?.abort();

  /** After an error nothing was stored, so the question is simply sent again. */
  const retry = (idx: number) => {
    const q = msgs[idx]?.retry;
    if (!q) return;
    setMsgs((all) => all.slice(0, Math.max(0, idx - 1)));
    void send(q);
  };

  const rate = async (idx: number, rating: "up" | "down") => {
    const m = msgs[idx];
    if (!m?.id) return;
    setMsgs((all) => all.map((x, i) => (i === idx ? { ...x, rating } : x)));
    try { await edrak.rateAnswer(m.id, rating); } catch { /* the rating is best effort */ }
  };

  const copy = async (idx: number) => {
    try {
      await navigator.clipboard.writeText(msgs[idx].content);
      setCopied(idx);
      setTimeout(() => setCopied((c) => (c === idx ? null : c)), 1500);
    } catch { /* clipboard blocked */ }
  };

  const addToBrief = async (idx: number) => {
    if (!scope.briefId || !scope.cardKey) return;
    try {
      const r = await edrak.challengeCard(scope.briefId, scope.cardKey, { source: "web", text: msgs[idx].content });
      setMsgs((all) => all.map((m, i) => (i === idx ? { ...m, addedNote: r.changed ? L(`Checked and added as card version ${r.version}.`, `تم التحقق وأُضيف كنسخة ${r.version}.`) : r.message ?? "" } : m)));
    } catch (e) {
      setMsgs((all) => all.map((m, i) => (i === idx ? { ...m, addedNote: (e as Error).message } : m)));
    }
  };

  const openHistory = async () => {
    if (history) { setHistory(null); return; }
    try {
      setHistory(await edrak.threads(scope.briefId ? { briefId: scope.briefId } : { scope: "workspace" }));
    } catch {
      setHistory([]);
    }
  };

  const openThread = async (t: ChatThreadRow) => {
    try {
      const r = await edrak.messages(t.id);
      setThread(t.id);
      setMsgs(r.messages.map(fromRow));
      setHistory(null);
    } catch (e) {
      setMsgs([{ role: "assistant", content: (e as Error).message, error: true }]);
      setHistory(null);
    }
  };

  const removeThread = async (t: ChatThreadRow) => {
    try {
      await edrak.deleteThread(t.id);
      setHistory((h) => (h ?? []).filter((x) => x.id !== t.id));
      if (thread === t.id) { setThread(null); setMsgs([]); }
    } catch { /* leave the list as it is */ }
  };

  const newConversation = () => { stop(); setMsgs([]); setThread(null); setHistory(null); input.current?.focus(); };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(text); }
    if (e.key === "Escape" && busy) { e.preventDefault(); stop(); }
  };

  const scopeLine = kind === "card" ? L("This card first, then the brief, then the web", "هذه البطاقة أولًا، ثم الموجز، ثم الويب")
    : kind === "signal" ? L("This signal and the brief, then the web", "هذه الإشارة والموجز، ثم الويب")
    : kind === "brief" ? L("This brief first, then your other sources", "هذا الموجز أولًا، ثم مصادرك الأخرى")
    : L("Your briefs, company and documents", "موجزاتك وشركتك ومستنداتك");
  const lastAssistant = msgs.map((m) => m.role).lastIndexOf("assistant");

  return (
    <aside className={`chat ${variant}`} role="dialog" aria-label={L("Chat with Edrak", "تحدث مع إدراك")}>
      <header className="chat-head">
        <span className="chat-avatar"><ThinkingMark size={18} live={!!busy} color="#F4F3EE" /></span>
        <div className="stack grow" style={{ minWidth: 0 }}>
          <b className="s14">{L("Ask Edrak", "اسأل إدراك")}</b>
          <span className="s12 t3 ellip">{scope.title && kind !== "brief" ? scope.title : scopeLine}</span>
        </div>
        <button type="button" className={`iconbtn sm ${history ? "on" : ""}`} aria-label={L("Past conversations", "المحادثات السابقة")} title={L("Past conversations", "المحادثات السابقة")} aria-pressed={!!history} onClick={() => void openHistory()}><Icon name="menu" size={14} /></button>
        {(msgs.length > 0 || history) && <button type="button" className="iconbtn sm" aria-label={L("New conversation", "محادثة جديدة")} title={L("New conversation", "محادثة جديدة")} onClick={newConversation}><Icon name="plus" size={14} /></button>}
        <button type="button" className="iconbtn sm" aria-label={L("Close", "إغلاق")} onClick={onClose}><Icon name="x" size={15} /></button>
      </header>

      <div ref={list} className="chat-msgs" aria-live="polite">
        {history && (
          <div className="chat-history">
            <b className="s13">{L("Past conversations", "المحادثات السابقة")}</b>
            {history.length === 0 && <span className="s13 t3">{L("No saved conversations yet.", "لا توجد محادثات محفوظة بعد.")}</span>}
            {history.map((t) => (
              <div key={t.id} className={`chat-history-row ${t.id === thread ? "on" : ""}`}>
                <button type="button" className="grow" style={{ minWidth: 0, textAlign: "start" }} onClick={() => void openThread(t)}>
                  <bdi className="b6 s13 ellip" style={{ display: "block" }}>{t.title || t.last_question || L("Conversation", "محادثة")}</bdi>
                  <span className="s12 t3">{t.card_key ? L("Card · ", "بطاقة · ") : t.signal_id ? L("Signal · ", "إشارة · ") : ""}{L(`${Math.ceil(t.message_count / 2)} questions`, `${Math.ceil(t.message_count / 2)} أسئلة`)} · {String(t.updated_at).slice(0, 10)}</span>
                </button>
                <button type="button" className="iconbtn sm" aria-label={L("Delete conversation", "حذف المحادثة")} title={L("Delete", "حذف")} onClick={() => void removeThread(t)}><Icon name="trash" size={13} /></button>
              </div>
            ))}
          </div>
        )}

        {!history && msgs.length === 0 && (
          <div className="chat-empty">
            <span className="chat-hero"><ThinkingMark size={44} live={false} color="var(--ink)" /></span>
            <b className="disp chat-hello">{kind === "card" || kind === "signal" ? L("Ask about this", "اسأل عن هذا") : L("What would you like to know?", "ماذا تريد أن تعرف؟")}</b>
            <span className="s13 t2 lh14">{kind === "workspace"
              ? L("Answers come from your briefs, your company profile and approved facts, and your documents, each with its source. Before any analysis has run, Edrak answers from what you told it, and anything from the web is marked unverified.",
                  "الإجابات من موجزاتك وملف شركتك والحقائق المعتمدة ومستنداتك، كل منها بمصدره. قبل أي تحليل يجيب إدراك مما أخبرته به، وأي شيء من الويب يُعلَّم غير موثّق.")
              : L("Answers come from the brief with their sources. When the brief does not cover it, Edrak looks at your other briefs and company profile, says so, and anything from the web is marked unverified.",
                  "الإجابات من الموجز مع مصادرها. إن لم يغطّها الموجز يبحث إدراك في موجزاتك الأخرى وملف شركتك ويقول ذلك، وأي شيء من الويب يُعلَّم غير موثّق.")}</span>
            <div className="chat-prompts">
              {suggestions.map((s) => (
                <button key={s} type="button" className="prompt-chip" onClick={() => void send(s)}>
                  <Icon name="sparkle" size={12} /> <span dir="auto">{s}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {!history && msgs.map((m, i) => m.role === "user" ? (
          <div key={i} className="bubble user" dir="auto">{m.content}</div>
        ) : (
          <div key={i} className={`bubble assistant ${m.error ? "error" : ""}`}>
            <span className="bubble-mark"><ThinkingMark size={14} live={!!m.streaming} color="var(--ink)" /></span>
            <div className="stack gap8 grow" style={{ minWidth: 0 }}>
              {!m.streaming && <SourceLabel label={m.label} kinds={m.kinds} kind={kind} />}
              {m.error ? <span className="chat-text" dir="auto">{m.content}</span> : <Markdown text={m.content} sources={m.sources} onEvidence={onEvidence} />}
              {m.stopped && <span className="s12 t3">{L("Stopped. This answer was not saved.", "توقّف. لم تُحفظ هذه الإجابة.")}</span>}
              {m.notes?.filter(Boolean).map((n) => <span key={n} className="s12 t3">{n}</span>)}
              {m.proposal && <span className="s12 t2"><b>{L("Worth researching next", "يستحق البحث لاحقًا")}:</b> {m.proposal}</span>}
              {m.sources && m.sources.length > 0 && <Sources sources={m.sources} onEvidence={onEvidence} />}
              {m.label === "web_unverified" && scope.cardKey && !m.addedNote && (
                <div><button type="button" className="btn ghost sm" onClick={() => void addToBrief(i)}><Icon name="plus" size={12} /> {L("Add to brief (checks it first)", "أضف إلى الموجز (بعد التحقق)")}</button></div>
              )}
              {m.addedNote && <span className="s12 t2">{m.addedNote}</span>}
              {m.error && m.retry && !busy && (
                <div><button type="button" className="btn ghost sm" onClick={() => retry(i)}><Icon name="refresh" size={12} /> {L("Try again", "حاول مجددًا")}</button></div>
              )}
              {!m.error && !m.streaming && !m.stopped && (
                <div className="chat-actions">
                  <button type="button" className="iconbtn xs" title={copied === i ? L("Copied", "نُسخ") : L("Copy", "نسخ")} aria-label={L("Copy answer", "نسخ الإجابة")} onClick={() => void copy(i)}>
                    <Icon name={copied === i ? "check" : "copy"} size={12} />
                  </button>
                  {m.id && <>
                    <button type="button" className={`iconbtn xs ${m.rating === "up" ? "on" : ""}`} aria-pressed={m.rating === "up"} title={L("Helpful", "مفيد")} aria-label={L("Helpful", "مفيد")} onClick={() => void rate(i, "up")}><Thumb /></button>
                    <button type="button" className={`iconbtn xs ${m.rating === "down" ? "on" : ""}`} aria-pressed={m.rating === "down"} title={L("Not helpful", "غير مفيد")} aria-label={L("Not helpful", "غير مفيد")} onClick={() => void rate(i, "down")}><Thumb down /></button>
                  </>}
                  {i === lastAssistant && thread && !busy && m.label !== "declined" && (
                    <button type="button" className="iconbtn xs" title={L("Answer again", "أجب مجددًا")} aria-label={L("Answer again", "أجب مجددًا")} onClick={() => void send("", { regenerate: true })}><Icon name="refresh" size={12} /></button>
                  )}
                </div>
              )}
              {i === lastAssistant && !busy && !m.error && (m.followUps?.length ?? 0) > 0 && (
                <div className="chat-follow">
                  {m.followUps!.map((q) => (
                    <button key={q} type="button" className="prompt-chip sm" onClick={() => void send(q)}>
                      <Icon name="arrow" size={12} flip /> <span dir="auto">{q}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
        {busy && !(msgs[msgs.length - 1]?.streaming) && !history && (
          <div className="bubble assistant thinking">
            <span className="bubble-mark"><ThinkingMark size={16} live color="var(--blue)" /></span>
            <span className="thinking-text">{busy}<span className="dots"><i /><i /><i /></span></span>
          </div>
        )}
      </div>

      <form className="chat-input" onSubmit={(e) => { e.preventDefault(); void send(text); }}>
        <textarea ref={input} rows={1} value={text} onChange={(e) => setText(e.target.value)} onKeyDown={onKey} dir="auto" maxLength={4000}
          placeholder={kind === "card" ? L("Ask about this card…", "اسأل عن هذه البطاقة…") : kind === "workspace" ? L("Ask about your company, briefs and sources…", "اسأل عن شركتك وموجزاتك ومصادرك…") : L("Ask about this brief…", "اسأل عن هذا الموجز…")} />
        {busy ? (
          <button type="button" className="send stop" onClick={stop} aria-label={L("Stop", "إيقاف")} title={L("Stop (Esc)", "إيقاف (Esc)")}>
            <Icon name="pause" size={15} stroke={2.2} />
          </button>
        ) : (
          <button type="submit" className="send" disabled={!text.trim()} aria-label={L("Send", "إرسال")} title={L(`Send (Enter). New line: Shift+Enter`, `إرسال (Enter). سطر جديد: Shift+Enter`)}>
            <Icon name="arrowUp" size={16} stroke={2.2} />
          </button>
        )}
      </form>
      <span className="chat-foot s12 t3">{L(`Enter to send · Shift+Enter for a new line · Esc to stop · ${modKey()}J to open or close`, `Enter للإرسال · Shift+Enter لسطر جديد · Esc للإيقاف`)}</span>
    </aside>
  );
}
