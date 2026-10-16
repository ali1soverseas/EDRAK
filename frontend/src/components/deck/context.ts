import { createContext, useContext } from "react";
import type { BriefV3 } from "../../types/brief3";

export interface DeckApi {
  brief: BriefV3;
  /** Open a card's detail drawer. */
  openCard: (cardKey: string) => void;
  /** Open one evidence item (its E-number). */
  openEvidence: (no: number) => void;
  /** Open a signal's detail drawer. */
  openSignal: (signalId: string) => void;
  /** Open the chat beside the deck, about one card or signal, or the whole brief. */
  ask: (scope?: { cardKey?: string; signalId?: string; title?: string }) => void;
  /** Jump to a slide by its id. */
  goTo: (slideId: string) => void;
  reload: () => void;
}

export const DeckContext = createContext<DeckApi | null>(null);

export function useDeck(): DeckApi {
  const v = useContext(DeckContext);
  if (!v) throw new Error("useDeck must be used inside the brief deck.");
  return v;
}

export const LENS: Record<string, { en: string; ar: string; css: string }> = {
  competitor_intelligence: { en: "Competitors", ar: "المنافسون", css: "comp" },
  market_intelligence: { en: "Market", ar: "السوق", css: "mkt" },
  customer_trends: { en: "Customers", ar: "العملاء", css: "cust" },
  internal_intelligence: { en: "Your company", ar: "شركتك", css: "int" },
};
