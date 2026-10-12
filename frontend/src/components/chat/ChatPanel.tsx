import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { modKey } from "../../lib/platform";
import { useL } from "../../lib/useL";
import { edrak } from "../../services/edrak";
import { Icon } from "../ui/Icon";
import { ThinkingMark } from "../ui/ThinkingMark";

export interface ChatScope { briefId?: string; cardKey?: string; signalId?: string; title?: string; nonce?: number }

type Label = "brief" | "web_unverified" | "not_covered";
interface Msg { role: "user" | "assistant"; content: string; label?: Label; notes?: string[]; proposal?: string | null; addedNote?: string; error?: boolean }

/** Answer text with [E12] turned into source chips. */
function Answer({ text, onEvidence }: { text: string; onEvidence?: (n: number) => void }) {
  const parts = text.split(/(\[E\d+\])/g);
  return (
    <span className="chat-text" dir="auto">
      {parts.map((p, i) => {
        const m = p.match(/^\[E(\d+)\]$/);
        if (!m) return <span key={i}>{p}</span>;
        const n = +m[1];
        return onEvidence
          ? <button key={i} type="button" className="ev" onClick={() => onEvidence(n)}>{`E${n}`}</button>
          : <span key={i} className="ev">{`E${n}`}</span>;
      })}
    </span>
  );
}

function SourceLabel({ label }: { label?: Label }) {
  const L = useL();
  if (label === "brief") return <span className="chip c-ok"><Icon name="check" size={11} /> {L("From the brief", "من الموجز")}</span>;
  if (label === "web_unverified") return <span className="chip c-ins"><Icon name="globe" size={11} /> {L("From the web, unverified", "من الويب، غير موثّق")}</span>;
  if (label === "not_covered") return <span className="chip c-draft"><Icon name="info" size={11} /> {L("Not covered by the brief", "غير مغطّى في الموجز")}</span>;
  return null;
}

/**
 * Chat with Edrak. Docked beside the brief (works in full screen) or floating over the app.
 * Brief first, then the web; every answer says which. A card's chat starts from that card.
 */
export function ChatPanel({ scope, variant, onClose, onEvidence }: {
  scope: ChatScope; variant: "docked" | "floating"; onClose: () => void; onEvidence?: (n: number) => void;
}) {
  const L = useL();
  const [thread, setThread] = useState<string | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const list = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const kind = scope.cardKey ? "card" : scope.signalId ? "signal" : scope.briefId ? "brief" : "workspace";

  useEffect(() => { input.current?.focus(); }, []);
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
      L("What changed with our competitors recently?", "ما الذي تغيّر لدى منافسينا مؤخرًا؟"),
      L("What do customers complain about most?", "ما أكثر ما يشتكي منه العملاء؟"),
      L("Summarise my latest brief", "لخّص أحدث موجز لي"),
    ];

  const send = async (content: string) => {
    content = content.trim();
    if (!content || busy) return;
    setText("");
    setMsgs((m) => [...m, { role: "user", content }]);
    setBusy(L("Reading the brief", "يقرأ الموجز"));
    try {
      let id = thread;
      if (!id) {
        const body = scope.briefId
          ? (scope.cardKey ? { scope: "card", brief_id: scope.briefId, card_key: scope.cardKey } : { scope: "brief", brief_id: scope.briefId })
          : { scope: "workspace" };
        id = (await edrak.newThread(body)).thread_id;
        setThread(id);
      }
      if (scope.briefId) {
        let answered = false;
        await edrak.streamAsk(id, content, (e) => {
          if (e.type === "step") {
            const tool = String(e.data.tool ?? "");
            setBusy(tool === "web_search" ? L("Searching the web", "يبحث في الويب") : tool === "get_metric" || tool === "get_card" ? L("Reading the figures", "يقرأ الأرقام") : L("Searching the brief", "يبحث في الموجز"));
          } else if (e.type === "answer") {
            answered = true;
            setMsgs((m) => [...m, { role: "assistant", content: String(e.data.answer ?? ""), label: e.data.label as Label, notes: e.data.notes as string[], proposal: e.data.proposal as string | null }]);
          } else if (e.type === "error") {
            answered = true;
            setMsgs((m) => [...m, { role: "assistant", content: String(e.data.message ?? ""), error: true }]);
          }
        });
        if (!answered) setMsgs((m) => [...m, { role: "assistant", content: L("No answer came back. Try again.", "لم يصل رد. حاول مجددًا."), error: true }]);
      } else {
        setBusy(L("Thinking", "يفكر"));
        const reply = await edrak.ask(id, content);
        setMsgs((m) => [...m, { role: "assistant", content: String(reply.content ?? "") }]);
      }
    } catch (e) {
      setMsgs((m) => [...m, { role: "assistant", content: (e as Error).message || L("Something went wrong.", "حدث خطأ."), error: true }]);
    } finally {
      setBusy(null);
    }
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

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(text); }
  };

  const scopeLine = kind === "card" ? L("This card first, then the brief, then the web", "هذه البطاقة أولًا، ثم الموجز، ثم الويب")
    : kind === "signal" ? L("This signal and the brief, then the web", "هذه الإشارة والموجز، ثم الويب")
    : kind === "brief" ? L("This brief first, then the web", "هذا الموجز أولًا، ثم الويب") : L("Your briefs and sources", "موجزاتك ومصادرك");

  return (
    <aside className={`chat ${variant}`} role="dialog" aria-label={L("Chat with Edrak", "تحدث مع إدراك")}>
      <header className="chat-head">
        <span className="chat-avatar"><ThinkingMark size={18} live={!!busy} color="#F4F3EE" /></span>
        <div className="stack grow" style={{ minWidth: 0 }}>
          <b className="s14">{L("Ask Edrak", "اسأل إدراك")}</b>
          <span className="s12 t3 ellip">{scope.title && kind !== "brief" ? scope.title : scopeLine}</span>
        </div>
        {msgs.length > 0 && <button type="button" className="iconbtn sm" aria-label={L("New conversation", "محادثة جديدة")} title={L("New conversation", "محادثة جديدة")} onClick={() => { setMsgs([]); setThread(null); }}><Icon name="refresh" size={14} /></button>}
        <button type="button" className="iconbtn sm" aria-label={L("Close", "إغلاق")} onClick={onClose}><Icon name="x" size={15} /></button>
      </header>

      <div ref={list} className="chat-msgs">
        {msgs.length === 0 && (
          <div className="chat-empty">
            <span className="chat-hero"><ThinkingMark size={44} live={false} color="var(--ink)" /></span>
            <b className="disp chat-hello">{kind === "card" || kind === "signal" ? L("Ask about this", "اسأل عن هذا") : L("What would you like to know?", "ماذا تريد أن تعرف؟")}</b>
            <span className="s13 t2 lh14">{L("Answers come from the brief with their sources. When the brief does not cover it, Edrak says so, and anything from the web is marked unverified.", "الإجابات من الموجز مع مصادرها. إن لم يغطّها الموجز يقول إدراك ذلك، وأي شيء من الويب يُعلَّم غير موثّق.")}</span>
            <div className="chat-prompts">
              {suggestions.map((s) => (
                <button key={s} type="button" className="prompt-chip" onClick={() => void send(s)}>
                  <Icon name="sparkle" size={12} /> <span dir="auto">{s}</span>
                </button>
              ))}
            </div>
          </div>
        )}
        {msgs.map((m, i) => m.role === "user" ? (
          <div key={i} className="bubble user" dir="auto">{m.content}</div>
        ) : (
          <div key={i} className={`bubble assistant ${m.error ? "error" : ""}`}>
            <span className="bubble-mark"><ThinkingMark size={14} live={false} color="var(--ink)" /></span>
            <div className="stack gap8 grow" style={{ minWidth: 0 }}>
              {m.label && <SourceLabel label={m.label} />}
              <Answer text={m.content} onEvidence={onEvidence} />
              {m.notes?.filter(Boolean).map((n) => <span key={n} className="s12 t3">{n}</span>)}
              {m.proposal && <span className="s12 t2"><b>{L("Worth researching next", "يستحق البحث لاحقًا")}:</b> {m.proposal}</span>}
              {m.label === "web_unverified" && scope.cardKey && !m.addedNote && (
                <div><button type="button" className="btn ghost sm" onClick={() => void addToBrief(i)}><Icon name="plus" size={12} /> {L("Add to brief (checks it first)", "أضف إلى الموجز (بعد التحقق)")}</button></div>
              )}
              {m.addedNote && <span className="s12 t2">{m.addedNote}</span>}
            </div>
          </div>
        ))}
        {busy && (
          <div className="bubble assistant thinking" aria-live="polite">
            <span className="bubble-mark"><ThinkingMark size={16} live color="var(--blue)" /></span>
            <span className="thinking-text">{busy}<span className="dots"><i /><i /><i /></span></span>
          </div>
        )}
      </div>

      <form className="chat-input" onSubmit={(e) => { e.preventDefault(); void send(text); }}>
        <textarea ref={input} rows={1} value={text} onChange={(e) => setText(e.target.value)} onKeyDown={onKey} dir="auto"
          placeholder={kind === "card" ? L("Ask about this card…", "اسأل عن هذه البطاقة…") : kind === "workspace" ? L("Ask about your briefs and sources…", "اسأل عن موجزاتك ومصادرك…") : L("Ask about this brief…", "اسأل عن هذا الموجز…")} />
        <button type="submit" className="send" disabled={!!busy || !text.trim()} aria-label={L("Send", "إرسال")} title={L(`Send (Enter). New line: Shift+Enter`, `إرسال (Enter). سطر جديد: Shift+Enter`)}>
          <Icon name="arrowUp" size={16} stroke={2.2} />
        </button>
      </form>
      <span className="chat-foot s12 t3">{L(`Enter to send · Shift+Enter for a new line · ${modKey()}J to open or close`, `Enter للإرسال · Shift+Enter لسطر جديد`)}</span>
    </aside>
  );
}
