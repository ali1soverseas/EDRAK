import { useCallback, useEffect, useRef, useState, type ChangeEvent, type DragEvent } from "react";
import { useNavigate } from "react-router-dom";
import { PageChrome } from "../components/shell/AppShell";
import { Chip } from "../components/ui/Chip";
import { ChoiceChips, TagInput } from "../components/ui/Controls";
import { Icon } from "../components/ui/Icon";
import { WorkerBadge } from "../components/ui/WorkerBadge";
import { useI18n, type TKey } from "../i18n";
import { newId } from "../lib/ids";
import { useAsync } from "../lib/hooks";
import { useDismiss } from "../lib/useDismiss";
import { api } from "../services/api";
import { useAuth } from "../state/auth";
import type { CompanyDocument, CompanyFields, CompanyLink, Industry, SocialPlatform } from "../types/app";

const INDUSTRIES: Industry[] = ["fintech", "saas", "retail", "logistics", "other"];

const PLATFORMS: Array<{ id: SocialPlatform; name: string; glyph: string; placeholder: string }> = [
  { id: "facebook", name: "Facebook", glyph: "f", placeholder: "https://facebook.com/your-page" },
  { id: "instagram", name: "Instagram", glyph: "ig", placeholder: "https://instagram.com/your-handle" },
  { id: "x", name: "X", glyph: "X", placeholder: "https://x.com/your-handle" },
  { id: "youtube", name: "YouTube", glyph: "yt", placeholder: "https://youtube.com/@your-channel" },
  { id: "linkedin", name: "LinkedIn", glyph: "in", placeholder: "https://linkedin.com/company/your-company" },
];

const platformOf = (id: SocialPlatform) => PLATFORMS.find((platform) => platform.id === id)!;

const ACCEPT = ".pdf,.doc,.docx,.txt,.md,.csv";

function PlatformBadge({ platform, small = false }: { platform: SocialPlatform; small?: boolean }) {
  return (
    <span className={`plat ${small ? "small" : ""}`.trim()} aria-hidden="true">
      {platformOf(platform).glyph}
    </span>
  );
}

function normalizeUrl(url: string): string {
  const trimmed = url.trim();
  if (!trimmed) return "";
  return /^https?:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`;
}

function SocialMenu({ onPick }: { onPick: (platform: SocialPlatform) => void }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useDismiss(ref, open, useCallback(() => setOpen(false), []));

  return (
    <div className="rel" ref={ref} style={{ marginTop: 2 }}>
      <button
        type="button"
        className="tag social-add"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <Icon name="plus" size={14} />
        {t("company.addSocial")}
        <Icon name="chevronDown" size={14} />
      </button>
      {open && (
        <div className="pop menu" role="menu">
          {PLATFORMS.map((platform) => (
            <button
              key={platform.id}
              type="button"
              role="menuitem"
              className="menu-item"
              onClick={() => {
                onPick(platform.id);
                setOpen(false);
              }}
            >
              <PlatformBadge platform={platform.id} small />
              {platform.name}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function documentSubtitle(doc: CompanyDocument, t: ReturnType<typeof useI18n>["t"], formatNumber: (n: number) => string): string {
  if (doc.status === "failed") return t("company.docs.failed");
  if (!doc.size) return "";
  if (doc.size.unit === "pages") return t("company.docs.pages", { n: formatNumber(doc.size.value) });
  if (doc.size.unit === "rows") return t("company.docs.rows", { n: formatNumber(doc.size.value) });
  return t("company.docs.kb", { n: formatNumber(doc.size.value) });
}

function DocumentRow({ doc, onRemove }: { doc: CompanyDocument; onRemove: () => void }) {
  const { t, formatNumber } = useI18n();
  return (
    <div className="doc-row">
      <span className="doc-icon">
        <Icon name="file" size={17} />
      </span>
      <div className="grow" style={{ minWidth: 0 }}>
        <div className="b6 s13 ellip">{doc.filename}</div>
        <div className="s12 t3">{documentSubtitle(doc, t, formatNumber)}</div>
      </div>
      {doc.status === "indexed" && (
        <Chip tone="ok" icon="check">
          {t("company.docs.indexed")}
        </Chip>
      )}
      {doc.status === "indexing" && (
        <Chip tone="run" icon="refresh" spinning>
          {t("company.docs.indexing")}
        </Chip>
      )}
      {doc.status === "failed" && (
        <>
          <Chip tone="bad" icon="x">
            {t("company.docs.failedChip")}
          </Chip>
          <button type="button" className="btn sm ghost icon" aria-label={t("common.remove", { name: doc.filename })} onClick={onRemove}>
            <Icon name="x" size={15} />
          </button>
        </>
      )}
    </div>
  );
}

export function Company() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { refresh } = useAuth();
  const company = useAsync(() => api.getCompany(), []);
  const [fields, setFields] = useState<CompanyFields | null>(null);
  const [documents, setDocuments] = useState<CompanyDocument[]>([]);
  const [wasCompleted, setWasCompleted] = useState(false);
  const [saving, setSaving] = useState(false);
  const [nameError, setNameError] = useState(false);
  const [saveError, setSaveError] = useState(false);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const loaded = company.data;
    if (!loaded || fields) return;
    const { documents: docs, completed, ...rest } = loaded;
    setFields(rest);
    setDocuments(docs);
    setWasCompleted(completed);
  }, [company.data, fields]);

  // While any file is being read, ask again until every file has a final status.
  const anyIndexing = documents.some((doc) => doc.status === "indexing");
  useEffect(() => {
    if (!anyIndexing) return;
    const timer = window.setInterval(() => {
      api.listDocuments().then(setDocuments, () => undefined);
    }, 1200);
    return () => window.clearInterval(timer);
  }, [anyIndexing]);

  const update = useCallback(<K extends keyof CompanyFields>(key: K, value: CompanyFields[K]) => {
    setFields((current) => (current ? { ...current, [key]: value } : current));
    if (key === "name") setNameError(false);
  }, []);

  const upload = async (files: FileList | File[]) => {
    const list = Array.from(files);
    if (list.length === 0) return;
    const added = await api.uploadDocuments(list);
    setDocuments((current) => [...current, ...added]);
  };

  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    setDragging(false);
    void upload(event.dataTransfer.files);
  };

  const save = async (after: "later" | "continue") => {
    if (!fields) return;
    if (after === "continue" && !fields.name.trim()) {
      setNameError(true);
      document.getElementById("company-name")?.focus();
      return;
    }
    setSaving(true);
    setSaveError(false);
    try {
      await api.saveCompany({
        ...fields,
        website: normalizeUrl(fields.website),
        socials: fields.socials.map((link) => ({ ...link, url: normalizeUrl(link.url) })).filter((link) => link.url),
      });
      await refresh();
      navigate(after === "continue" ? "/analyses/new" : "/");
    } catch {
      setSaveError(true);
      setSaving(false);
    }
  };

  const chrome = <PageChrome crumbs={[{ label: t("nav.company") }]} title={t("nav.company")} />;

  if (company.error) {
    return (
      <>
        {chrome}
        <div className="form-error" role="alert">
          <Icon name="warning" size={16} />
          <span>{t("common.error.load")}</span>
          <button type="button" className="btn sm" onClick={company.reload}>
            {t("common.retry")}
          </button>
        </div>
      </>
    );
  }

  if (!fields) {
    return (
      <>
        {chrome}
        <div className="company-grid" aria-busy="true">
          <div className="col g20">
            <div className="shim" style={{ height: 44, width: 420 }} />
            <div className="shim" style={{ height: 20, width: 560 }} />
            <div className="shim" style={{ height: 300 }} />
          </div>
          <div className="col g16">
            <div className="shim" style={{ height: 280 }} />
            <div className="shim" style={{ height: 300 }} />
          </div>
        </div>
      </>
    );
  }

  const addSocial = (platform: SocialPlatform) =>
    update("socials", [...fields.socials, { link_id: newId(), platform, url: "" } satisfies CompanyLink]);
  const setSocial = (id: string, url: string) =>
    update("socials", fields.socials.map((link) => (link.link_id === id ? { ...link, url } : link)));

  return (
    <>
      {chrome}
      <div className="company-grid">
        <div className="col company-main">
          <div className="eyebrow">{t(wasCompleted ? "company.eyebrow" : "company.eyebrowFirst")}</div>
          <h1 className="disp" style={{ fontSize: 40, margin: "10px 0 8px" }}>
            {fields.name.trim() ? t("company.title", { name: fields.name.trim() }) : t("company.titleEmpty")}
          </h1>
          <p className="t2 s15 lh16" style={{ margin: "0 0 24px", maxWidth: 560 }}>
            {t("company.lede")}
          </p>

          <div className="col g20 grow">
            <div className="row g16 as split">
              <div className="grow">
                <label className="lbl" htmlFor="company-name">
                  {t("company.name")}
                </label>
                <input
                  id="company-name"
                  className="inp"
                  value={fields.name}
                  aria-invalid={nameError || undefined}
                  aria-describedby={nameError ? "company-name-error" : undefined}
                  onChange={(event: ChangeEvent<HTMLInputElement>) => update("name", event.target.value)}
                />
                {nameError && (
                  <div id="company-name-error" className="field-error">
                    {t("company.nameRequired")}
                  </div>
                )}
              </div>
              <div className="grow">
                <div className="lbl">{t("company.aliases")}</div>
                <TagInput values={fields.aliases} onChange={(values) => update("aliases", values)} label={t("company.aliases")} />
              </div>
            </div>

            <div>
              <div className="lbl">{t("company.industry")}</div>
              <ChoiceChips
                label={t("company.industry")}
                options={INDUSTRIES.map((value) => ({ value, label: t(`company.industry.${value}` as TKey) }))}
                selected={fields.industry ? [fields.industry] : []}
                onToggle={(value) => update("industry", fields.industry === value ? null : value)}
              />
            </div>

            <div>
              <label className="lbl" htmlFor="company-description">
                {t("company.description")}
              </label>
              <textarea
                id="company-description"
                className="inp"
                rows={2}
                value={fields.description}
                onChange={(event) => update("description", event.target.value)}
              />
            </div>

            <div className="row g16 as split">
              <div className="grow">
                <div className="lbl">{t("company.offerings")}</div>
                <TagInput values={fields.offerings} onChange={(values) => update("offerings", values)} label={t("company.offerings")} />
              </div>
              <div className="grow">
                <div className="lbl">{t("company.markets")}</div>
                <TagInput values={fields.markets} onChange={(values) => update("markets", values)} label={t("company.markets")} />
              </div>
            </div>

            <div>
              <label className="lbl" htmlFor="company-goals">
                {t("company.goals")}
              </label>
              <textarea
                id="company-goals"
                className="inp"
                rows={2}
                value={fields.strategic_goals}
                onChange={(event) => update("strategic_goals", event.target.value)}
              />
            </div>
          </div>

          <div className="company-foot">
            <span className="s13 t3">{saveError ? t("company.saveError") : t("company.privacy")}</span>
            <div className="row g10">
              <button type="button" className="btn" disabled={saving} onClick={() => void save("later")}>
                {t("company.saveLater")}
              </button>
              <button type="button" className="btn pri lg" disabled={saving} aria-busy={saving} onClick={() => void save("continue")}>
                {t("company.saveContinue")}
                <Icon name="arrow" size={18} flip />
              </button>
            </div>
          </div>
        </div>

        <aside className="col company-side">
          <section className="card col" style={{ padding: "20px 22px", gap: 14 }}>
            <div className="row g10">
              <span className="side-icon">
                <Icon name="globe" size={18} />
              </span>
              <div>
                <div className="b6 s15">{t("company.web.title")}</div>
                <div className="s12 t3">{t("company.web.sub")}</div>
              </div>
            </div>
            <div>
              <label className="lbl" htmlFor="company-website">
                {t("company.web.website")}
              </label>
              <input
                id="company-website"
                className="inp"
                inputMode="url"
                placeholder="https://"
                value={fields.website}
                onChange={(event) => update("website", event.target.value)}
              />
            </div>
            <div className="col" style={{ gap: 8 }}>
              <div className="lbl" style={{ margin: 0 }}>
                {t("company.web.socials")}
              </div>
              {fields.socials.map((link) => {
                const platform = platformOf(link.platform);
                return (
                  <div key={link.link_id} className="row g10">
                    <PlatformBadge platform={link.platform} />
                    <div className="col grow" style={{ gap: 3, minWidth: 0 }}>
                      <label className="s12 t3 b6" htmlFor={`social-${link.link_id}`}>
                        {platform.name}
                      </label>
                      <input
                        id={`social-${link.link_id}`}
                        className="inp"
                        style={{ height: 38 }}
                        inputMode="url"
                        value={link.url}
                        placeholder={platform.placeholder}
                        onChange={(event) => setSocial(link.link_id, event.target.value)}
                      />
                    </div>
                    <button
                      type="button"
                      className="btn sm ghost icon social-remove"
                      aria-label={t("common.remove", { name: platform.name })}
                      onClick={() => update("socials", fields.socials.filter((candidate) => candidate.link_id !== link.link_id))}
                    >
                      <Icon name="x" size={15} />
                    </button>
                  </div>
                );
              })}
              <SocialMenu onPick={addSocial} />
              <div className="help" style={{ marginTop: 2 }}>
                {t("company.web.help")}
              </div>
            </div>
          </section>

          <section className="card col" style={{ padding: "20px 22px", gap: 12 }}>
            <div className="row g10">
              <WorkerBadge worker="internal_intelligence" large iconSize={17} />
              <div>
                <div className="b6 s15">{t("company.files.title")}</div>
                <div className="s12 t3">{t("company.files.sub")}</div>
              </div>
            </div>
            <div
              className={`dropzone ${dragging ? "over" : ""}`.trim()}
              onDragOver={(event) => {
                event.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={onDrop}
            >
              <span className="doc-icon">
                <Icon name="upload" size={17} />
              </span>
              <div className="grow">
                <div className="b6 s13">
                  {t("company.files.drop")}{" "}
                  <button type="button" className="link" onClick={() => fileInput.current?.click()}>
                    {t("company.files.browse")}
                  </button>
                </div>
                <div className="s12 t3">{t("company.files.types")}</div>
              </div>
              <input
                ref={fileInput}
                type="file"
                className="sr-only"
                multiple
                accept={ACCEPT}
                tabIndex={-1}
                aria-label={t("company.files.browse")}
                onChange={(event) => {
                  void upload(event.target.files ?? []);
                  event.target.value = "";
                }}
              />
            </div>
            {documents.length > 0 && (
              <div className="col g8">
                {documents.map((doc) => (
                  <DocumentRow
                    key={doc.document_id}
                    doc={doc}
                    onRemove={() => {
                      setDocuments((current) => current.filter((candidate) => candidate.document_id !== doc.document_id));
                      void api.removeDocument(doc.document_id);
                    }}
                  />
                ))}
              </div>
            )}
          </section>
        </aside>
      </div>
    </>
  );
}
