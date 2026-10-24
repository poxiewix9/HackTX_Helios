// Generate ts/src/generated/arco.ts from schema/arco.schema.json.
// The JSON Schema bundle is produced by `python -m arco_schemas.export`; this script
// is the second half of `make schemas`. Do not edit the generated file by hand.
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { compile } from "json-schema-to-typescript";

const here = dirname(fileURLToPath(import.meta.url));
const pkgRoot = resolve(here, "..");
const bundlePath = resolve(pkgRoot, "schema/arco.schema.json");
const outPath = resolve(pkgRoot, "ts/src/generated/arco.ts");

const bundle = JSON.parse(await readFile(bundlePath, "utf8"));

// Normalise the Pydantic-flavoured schema so the TS mirrors the wire exactly:
//  - Pydantic puts a `title` on every property; json-schema-to-typescript would turn
//    each into a named alias (`Confidence1`, ...). Keep titles only on `$defs` entries.
//  - `$ref` with sibling keys (`description`, `default`) is treated as a *new* anonymous
//    type (`HazardClass1`); keep only the `$ref`.
//  - Literal discriminators (`type: Literal["Polygon"] = "Polygon"`) are optional in JSON
//    Schema because they have a default, but Pydantic always serialises them, so mark
//    single-value `const`/`enum` properties that have a default as required.
function isSingleValued(schema) {
  return (
    schema &&
    typeof schema === "object" &&
    ("const" in schema || (Array.isArray(schema.enum) && schema.enum.length === 1))
  );
}

function normaliseSchema(schema, { keepTitle = false } = {}) {
  if (!schema || typeof schema !== "object" || Array.isArray(schema)) return;
  if (!keepTitle) delete schema.title;

  if ("$ref" in schema) {
    for (const key of Object.keys(schema)) {
      if (key !== "$ref") Reflect.deleteProperty(schema, key);
    }
    return;
  }

  if (schema.properties && typeof schema.properties === "object") {
    const required = new Set(schema.required ?? []);
    for (const [name, prop] of Object.entries(schema.properties)) {
      if (isSingleValued(prop) && "default" in prop) required.add(name);
      normaliseSchema(prop);
    }
    if (required.size) schema.required = [...required].sort();
  }
  for (const key of ["items", "additionalProperties", "propertyNames", "not"]) {
    if (schema[key] && typeof schema[key] === "object") normaliseSchema(schema[key]);
  }
  for (const key of ["prefixItems", "anyOf", "oneOf", "allOf"]) {
    if (Array.isArray(schema[key])) for (const sub of schema[key]) normaliseSchema(sub);
  }
}

for (const def of Object.values(bundle.$defs)) normaliseSchema(def, { keepTitle: true });

const banner = [
  "/* eslint-disable */",
  "/**",
  " * GENERATED FILE - DO NOT EDIT.",
  ` * Source: packages/shared-schemas/schema/arco.schema.json (schema version ${bundle.version})`,
  " * Regenerate with `make schemas`.",
  " */",
  "",
].join("\n");

const ts = await compile(bundle, "ArcoSchemaBundle", {
  bannerComment: banner,
  additionalProperties: false,
  declareExternallyReferenced: true,
  strictIndexSignatures: true,
  unreachableDefinitions: true,
  style: { printWidth: 100, singleQuote: false, trailingComma: "all", semi: true },
});

await mkdir(dirname(outPath), { recursive: true });
await writeFile(outPath, ts, "utf8");
await writeFile(
  resolve(pkgRoot, "ts/src/generated/version.ts"),
  `${banner}export const SCHEMA_VERSION = ${JSON.stringify(bundle.version)} as const;\n`,
  "utf8",
);
console.log(`wrote ${outPath}`);
