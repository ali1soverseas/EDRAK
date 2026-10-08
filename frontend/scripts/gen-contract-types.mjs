// Turns src/types/contracts.schema.json (exported from the Pydantic contracts by
// scripts/export_contract_schemas.py) into src/types/contracts.ts.
//
// Usage: npm run gen:types
import { readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { compile } from "json-schema-to-typescript";

const schemaPath = fileURLToPath(new URL("../src/types/contracts.schema.json", import.meta.url));
const outPath = fileURLToPath(new URL("../src/types/contracts.ts", import.meta.url));

const schema = JSON.parse(await readFile(schemaPath, "utf8"));

/**
 * Pydantic writes a `title` on every field, and a `description` beside every `$ref`.
 * json-schema-to-typescript turns the first into a named alias per field and the
 * second into a copy of the referenced type, which gives names like
 * `BusinessContext2`. Neither carries meaning for the UI, so strip them. Only the
 * top-level definitions keep their title, which becomes the type name.
 */
function clean(node, isDefinition = false) {
  if (Array.isArray(node)) return node.map((child) => clean(child));
  if (node === null || typeof node !== "object") return node;
  if (typeof node.$ref === "string") return { $ref: node.$ref };

  const out = {};
  for (const [key, value] of Object.entries(node)) {
    if (key === "title" && !isDefinition) continue;
    if (key === "properties" || key === "$defs") {
      out[key] = Object.fromEntries(
        Object.entries(value).map(([name, child]) => [name, clean(child, key === "$defs")]),
      );
    } else if (key === "enum" || key === "required" || key === "default" || key === "examples") {
      out[key] = value;
    } else {
      out[key] = clean(value);
    }
  }
  return out;
}

const cleaned = clean(schema, true);

// One root property per exported model, so every nested type is emitted once.
const exported = ["BusinessRequest", "ResearchPlan", "ResearchTask", "WorkerResult", "OrchestrationResult", "VerificationResult"];
cleaned.type = "object";
cleaned.additionalProperties = false;
cleaned.properties = Object.fromEntries(exported.map((name) => [name, { $ref: `#/$defs/${name}` }]));
cleaned.required = exported;

const ts = await compile(cleaned, "EdrakContracts", {
  additionalProperties: false,
  bannerComment: [
    "/* eslint-disable */",
    "/**",
    " * GENERATED FILE. Do not edit by hand.",
    " * Source: backend/src/edrak/contracts (Pydantic models).",
    " * Regenerate: .venv/bin/python scripts/export_contract_schemas.py && npm run gen:types",
    " */",
  ].join("\n"),
  style: { singleQuote: false, semi: true },
});

await writeFile(outPath, ts, "utf8");
console.log(`wrote ${outPath}`);
