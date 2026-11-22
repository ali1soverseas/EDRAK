import { HttpError, edrak } from "../services/edrak";

type Phrase = (en: string, ar: string) => string;

/** Ask, then delete. Returns true when the brief is gone. A recorded decision keeps the brief. */
export async function deleteBrief(id: string, L: Phrase): Promise<boolean> {
  if (!confirm(L("Delete this brief? The analysis and its sources stay.", "حذف هذا الموجز؟ يبقى التحليل ومصادره."))) return false;
  try {
    await edrak.deleteBrief(id);
    return true;
  } catch (e) {
    const locked = e instanceof HttpError && e.status === 409;
    alert(locked
      ? L("A decision was recorded from this brief, so it cannot be deleted.", "سُجّل قرار من هذا الموجز، لذلك لا يمكن حذفه.")
      : L("Could not delete this brief.", "تعذر حذف هذا الموجز."));
    return false;
  }
}
