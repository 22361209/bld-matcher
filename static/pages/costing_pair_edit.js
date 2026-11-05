// The same single-position rule is checked by the server before a pair is written.
export function mirrorCostCode(value) {
  const code = String(value || "").trim();
  const matches = [...code.matchAll(/(?:^|(?<=[0-9-]))[LR](?=[ABC](?:[0-9-]|$))/gi)];
  if (matches.length !== 1) return "";
  const {index} = matches[0];
  const side = code[index].toUpperCase() === "L" ? "R" : "L";
  return code.slice(0, index) + side + code.slice(index + 1);
}

export function mirrorPartCode(value) {
  const code = String(value || "").trim();
  const match = /^([A-Z]+[0-9]{3,})([LR])(D?)$/i.exec(code);
  if (!match) return mirrorCostCode(code);
  return match[1] + (match[2].toUpperCase() === "L" ? "R" : "L") + match[3];
}

export function rowPairKind(item) {
  if (item.pair_kind) return item.pair_kind;
  const name = String(item.name || "");
  const directional = name.includes("左") !== name.includes("右") ||
    [item.product_code, item.part_code, name].some((value) => mirrorPartCode(value));
  return directional ? "handed" : "shared";
}

export const pairKindOptions = [["shared", "左右共用件"], ["handed", "左右对应件"], ["local", "本侧独有"]];

export function pairKindExplanation(item, originalKind) {
  const kind = rowPairKind(item);
  if (kind === "local") return originalKind && originalKind !== "local"
    ? "保存后两侧各自保留这一行，今后分别维护。" : "此行只计入当前型号。";
  if (originalKind === "local") return "保存时将在对侧新增对应行。对侧已有的独有行会单独保留。";
  if (originalKind === "handed" && kind === "shared") return "保存时将把本行的产品编码和零件编码同步到对侧。";
  return kind === "shared" ? "单价、产品编码和零件编码随两侧一起更新。" : "两侧共用单价，各自维护产品编码和零件编码。";
}
