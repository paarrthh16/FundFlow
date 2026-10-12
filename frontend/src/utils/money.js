// Money display (PRD D-23): exactly two decimals and "," thousands separators, no currency symbol.
// Works on the API's decimal strings ("25000.00") using string/BigInt arithmetic only, so amounts
// are never converted to JavaScript floating-point numbers.

const DECIMAL_RE = /^([+-])?(\d*)(?:\.(\d*))?$/;

export function formatMoney(value) {
  if (value === null || value === undefined) return "";
  const text = String(value).trim();
  const match = DECIMAL_RE.exec(text);
  if (text === "" || !match || (match[2] === "" && !match[3])) return text; // not a plain decimal: show as-is

  const [, sign, integerDigits, fractionDigits = ""] = match;

  // Amount in hundredths. The API always sends 2 decimals; extra digits are rounded half away from zero.
  let cents = BigInt((integerDigits || "0") + fractionDigits.slice(0, 2).padEnd(2, "0"));
  if (fractionDigits.length > 2 && fractionDigits[2] >= "5") cents += 1n;

  const whole = (cents / 100n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const hundredths = (cents % 100n).toString().padStart(2, "0");
  const negative = sign === "-" && cents !== 0n; // "-0.00" displays as "0.00"
  return `${negative ? "-" : ""}${whole}.${hundredths}`;
}
