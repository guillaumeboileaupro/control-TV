// The shapes data has at the boundary with the shell: JSON values, and values caught by `catch`.
//
// The shell answers with JSON. Its values are read through these types and narrowed by checks
// (`isJsonObject`, `typeof`), never asserted: what does not have the expected shape is treated
// as "not reported", never guessed.

export type JsonValue = string | number | boolean | null | JsonValue[] | JsonObject;

export interface JsonObject {
  [key: string]: JsonValue;
}

// Whatever a `catch` clause or a rejected promise can hold: any value except none at all.
export type Thrown = {} | null | undefined;

export function isJsonObject(value: JsonValue | undefined): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
