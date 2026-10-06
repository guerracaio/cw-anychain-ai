import Image from "next/image";
import openaiLogo from "../../assets/OAI_OpenAI-Blossom_White.svg";

/** Provider mark shown inside the model pill (white on the brand purple). */
export function LlmLogo({ provider }: { provider: string }) {
  if (provider === "openai") {
    // The mark has generous built-in padding, so it is drawn slightly larger than its slot.
    return <Image src={openaiLogo} alt="OpenAI" width={22} height={22} unoptimized className="-my-1 -ml-1 size-[22px]" />;
  }
  return <span aria-hidden="true">✦</span>;
}
