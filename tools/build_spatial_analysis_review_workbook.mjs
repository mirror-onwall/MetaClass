import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

const bundledNodeModules =
  process.env.CODEX_NODE_MODULES ||
  "C:\\Users\\17847\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\node\\node_modules";
const requireFromBundledDeps = createRequire(path.join(bundledNodeModules, ".codex-resolver.cjs"));
const artifactToolPath = requireFromBundledDeps.resolve("@oai/artifact-tool");
const { SpreadsheetFile, Workbook } = await import(pathToFileURL(artifactToolPath).href);

const inputDir = process.argv[2] || "output/spatial_analysis_kb";
const outputPath =
  process.argv[3] || path.join(inputDir, "spatial_analysis_kb_review.xlsx");

const lowTextCsv = await fs.readFile(path.join(inputDir, "low_text_pages.csv"), "utf8");
const chunkReviewCsv = await fs.readFile(path.join(inputDir, "chunk_review.csv"), "utf8");

const workbook = await Workbook.fromCSV(lowTextCsv, {
  sheetName: "Low Text Pages",
});
await workbook.fromCSV(chunkReviewCsv, {
  sheetName: "Chunk Review",
});

function setupSheet(sheetName, tableName, usedRange, headerFill) {
  const sheet = workbook.worksheets.getItem(sheetName);
  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(1);
  const range = sheet.getRange(usedRange);
  range.format.font = { name: "Aptos", size: 10 };
  sheet.getRange(usedRange.split(":")[0] + ":" + usedRange.split(":")[1].replace(/\d+$/, "1")).format = {
    fill: headerFill,
    font: { bold: true, color: "#FFFFFF" },
    wrapText: true,
    horizontalAlignment: "center",
  };
  range.format.borders = {
    insideHorizontal: { style: "thin", color: "#E5E7EB" },
    top: { style: "thin", color: "#CBD5E1" },
    bottom: { style: "thin", color: "#CBD5E1" },
  };
  sheet.tables.add(usedRange, true, tableName);
  return sheet;
}

const lowSheet = setupSheet("Low Text Pages", "LowTextPagesTable", "A1:M124", "#9F1239");
lowSheet.getRange("A1:A124").format.columnWidth = 24;
lowSheet.getRange("B1:B124").format.columnWidth = 20;
lowSheet.getRange("C1:C124").format.columnWidth = 45;
lowSheet.getRange("D1:H124").format.columnWidth = 12;
lowSheet.getRange("I1:J124").format.columnWidth = 22;
lowSheet.getRange("K1:K124").format.columnWidth = 58;
lowSheet.getRange("L1:M124").format.columnWidth = 20;
lowSheet.getRange("K1:K124").format.wrapText = true;
lowSheet.getRange("L2:L124").dataValidation = {
  rule: { type: "list", values: ["pending", "keep", "ocr_needed", "visual_asset", "ignore"] },
};

const chunkSheet = setupSheet("Chunk Review", "ChunkReviewTable", "A1:R1726", "#155E75");
chunkSheet.getRange("A1:B1726").format.columnWidth = 24;
chunkSheet.getRange("C1:C1726").format.columnWidth = 46;
chunkSheet.getRange("D1:D1726").format.columnWidth = 10;
chunkSheet.getRange("E1:F1726").format.columnWidth = 34;
chunkSheet.getRange("G1:H1726").format.columnWidth = 10;
chunkSheet.getRange("I1:K1726").format.columnWidth = 16;
chunkSheet.getRange("L1:L1726").format.columnWidth = 30;
chunkSheet.getRange("M1:M1726").format.columnWidth = 72;
chunkSheet.getRange("N1:R1726").format.columnWidth = 22;
chunkSheet.getRange("M1:M1726").format.wrapText = true;
chunkSheet.getRange("N2:N1726").dataValidation = {
  rule: { type: "list", values: ["yes", "no", "merge", "split", "revise"] },
};
chunkSheet.getRange("O2:O1726").dataValidation = {
  rule: { type: "list", values: ["yes", "no"] },
};
chunkSheet.getRange("Q2:Q1726").dataValidation = {
  rule: {
    type: "list",
    values: ["concept", "method", "formula", "workflow", "case", "summary"],
  },
};

await workbook.inspect({
  kind: "workbook,sheet,table",
  maxChars: 4000,
  tableMaxRows: 4,
  tableMaxCols: 6,
});

await fs.mkdir(path.dirname(outputPath), { recursive: true });
const lowPreview = await workbook.render({
  sheetName: "Low Text Pages",
  range: "A1:M25",
  scale: 1,
  format: "png",
});
await fs.writeFile(
  path.join(inputDir, "low_text_pages_preview.png"),
  new Uint8Array(await lowPreview.arrayBuffer()),
);
const chunkPreview = await workbook.render({
  sheetName: "Chunk Review",
  range: "A1:R25",
  scale: 1,
  format: "png",
});
await fs.writeFile(
  path.join(inputDir, "chunk_review_preview.png"),
  new Uint8Array(await chunkPreview.arrayBuffer()),
);
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(outputPath);
