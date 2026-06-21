// Tiny dependency-free className joiner (avoids pulling in the clsx package
// for one helper function).
export default function clsx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}
