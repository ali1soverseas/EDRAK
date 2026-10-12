import { useEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type ReactNode } from "react";
import { useI18n } from "../../i18n";
import { Icon } from "./Icon";

/* -------------------------------------------------------------------- Toggle */

interface ToggleProps {
  on: boolean;
  onChange: (next: boolean) => void;
  label: string;
  disabled?: boolean;
}

export function Toggle({ on, onChange, label, disabled }: ToggleProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      disabled={disabled}
      className={`tog ${on ? "on" : ""}`.trim()}
      onClick={() => onChange(!on)}
    />
  );
}

/* ----------------------------------------------------------------- Segmented */

export interface SegmentOption<T extends string> {
  value: T;
  label: ReactNode;
  style?: CSSProperties;
  ariaLabel?: string;
}

interface SegmentedProps<T extends string> {
  options: ReadonlyArray<SegmentOption<T>>;
  value: T;
  onChange: (value: T) => void;
  label: string;
}

export function Segmented<T extends string>({ options, value, onChange, label }: SegmentedProps<T>) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          className={option.value === value ? "on" : ""}
          aria-pressed={option.value === value}
          aria-label={option.ariaLabel}
          style={option.style}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/* --------------------------------------------------------------- ChoiceChips */

interface ChoiceOption<T extends string> {
  value: T;
  label: string;
}

interface ChoiceChipsProps<T extends string> {
  options: ReadonlyArray<ChoiceOption<T>>;
  /** Selected values. A single-choice group passes at most one. */
  selected: readonly T[];
  onToggle: (value: T) => void;
  label: string;
}

/** Selectable tags: industry, target customers, what to look at. */
export function ChoiceChips<T extends string>({ options, selected, onToggle, label }: ChoiceChipsProps<T>) {
  return (
    <div className="row g8 wrap" role="group" aria-label={label}>
      {options.map((option) => {
        const on = selected.includes(option.value);
        return (
          <button
            key={option.value}
            type="button"
            className={`tag choice ${on ? "on" : ""}`.trim()}
            aria-pressed={on}
            onClick={() => onToggle(option.value)}
          >
            {on && <Icon name="check" size={13} stroke={2.4} />}
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------------ TagInput */

interface TagInputProps {
  values: string[];
  onChange: (values: string[]) => void;
  /** Name for the group. Read by screen readers; shown above the tags when `showLabel` is set. */
  label: string;
  addLabel?: string;
  showLabel?: boolean;
  /** One line under the label saying what goes here. */
  hint?: string;
  /** Long entries (goals): tags wrap onto several lines and the field grows while typing. */
  wrap?: boolean;
  /** Colour of the tags, to tell two lists apart (markets you sell in vs. markets you consider). */
  tone?: "ok" | "blue";
}

/** A list of removable tags with an "Add" tag that turns into an input. */
export function TagInput({ values, onChange, label, addLabel, showLabel = false, hint, wrap = false, tone }: TagInputProps) {
  const { t } = useI18n();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLInputElement & HTMLTextAreaElement>(null);

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);
  useEffect(() => {
    const el = inputRef.current;
    if (!wrap || !el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [draft, wrap, editing]);

  const commit = () => {
    const value = draft.trim();
    if (value && !values.some((existing) => existing.toLowerCase() === value.toLowerCase())) {
      onChange([...values, value]);
    }
    setDraft("");
    setEditing(false);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    if (event.key === "Enter" || (event.key === "," && !wrap)) {
      event.preventDefault();
      commit();
    } else if (event.key === "Escape") {
      event.preventDefault();
      setDraft("");
      setEditing(false);
    }
  };

  const group = (
    <div className={`row g8 wrap tag-group ${wrap ? "wrap-tags" : ""} ${tone ? `tone-${tone}` : ""}`.trim()} role="group" aria-label={label}>
      {values.map((value) => (
        <span key={value} className="tag tag-item">
          <span className="tag-text" dir="auto">{value}</span>
          <button
            type="button"
            className="tag-x"
            aria-label={t("common.remove", { name: value })}
            onClick={() => onChange(values.filter((existing) => existing !== value))}
          >
            <Icon name="x" size={13} />
          </button>
        </span>
      ))}
      {editing ? (
        <span className="tag tag-item add editing">
          {wrap ? (
            <textarea ref={inputRef} rows={1} className="tag-field" value={draft} aria-label={label} dir="auto"
              onChange={(event) => setDraft(event.target.value)} onKeyDown={onKeyDown} onBlur={commit} />
          ) : (
            <input ref={inputRef} className="tag-field" value={draft} aria-label={label} dir="auto"
              onChange={(event) => setDraft(event.target.value)} onKeyDown={onKeyDown} onBlur={commit} />
          )}
        </span>
      ) : (
        <button type="button" className="tag tag-item add" onClick={() => setEditing(true)}>
          <Icon name="plus" size={13} />
          {addLabel ?? t("common.add")}
        </button>
      )}
    </div>
  );

  if (!showLabel && !hint) return group;
  return (
    <div className="field tag-input">
      {showLabel && <span>{label}</span>}
      {hint && <span className="s12 t3 field-hint">{hint}</span>}
      {group}
    </div>
  );
}
