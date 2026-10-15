import { describe, expect, it } from "vitest";
import corpus from "../fixtures/detect-corpus.json";
import {
  asksSomething,
  detectPrompt,
  EMOJI_RE,
  lastQuestion,
  lastRequest,
  normalize,
  PROMPT_KINDS,
  PROMPT_PATTERNS,
  zoneOptions,
  type Interactive,
  type PromptKind,
} from "../../src/stage/detect.ts";

// stage/detect.ts is a port of src/tests/sim/detect.py. The corpus holds the label that Python's
// `detect_prompt` gave each agent message (src/scripts/export_personas.py writes it), so parity
// on the corpus is the proof; the unit cases below pin the places where JavaScript's regular
// expressions differ from Python's. The expected values in them come from running the Python.

type Entry = { source: string; text: string; interactive: Interactive | null; label: string | null };
const entries = corpus.entries as Entry[];

// The same categories the Python script requires (`missing_categories`), detected the same way.
const platformPattern = PROMPT_PATTERNS.find(([kind]) => kind === "experience_platforms")![1];

function categoriesOf(entry: Entry): Set<string> {
  const found = new Set<string>([entry.label ? `kind:${entry.label}` : "no_label"]);
  if (entry.interactive) found.add("option_ids");
  const question = lastQuestion(entry.text);
  if (question !== null) found.add("question");
  else if (lastRequest(entry.text) !== null) found.add("request");
  if (entry.text.includes("etc.")) found.add("etc_shield");
  if (EMOJI_RE.test(entry.text)) found.add("emoji");
  if ([...entry.text].some((c) => /\p{L}/u.test(c) && c.codePointAt(0)! > 127)) found.add("non_ascii");
  if (entry.label === "experience_years" && question !== null && platformPattern.test(normalize(question))) {
    found.add("duration_vs_platform");
  }
  return found;
}

describe("detectPrompt on the corpus", () => {
  it("gives every entry the label the Python detector gave it", () => {
    expect(entries.length).toBeGreaterThanOrEqual(40);
    const mismatches = entries.flatMap((entry, index) => {
      const got = detectPrompt(entry.text, entry.interactive);
      return got === entry.label ? [] : [`entry ${index} (${entry.source}) ${JSON.stringify(entry.text)}: python ${entry.label}, ts ${got}`];
    });
    expect(mismatches, `${mismatches.length} mismatches`).toEqual([]);
  });

  it("covers the categories the export script requires", () => {
    const counts = new Map<string, number>();
    for (const entry of entries) {
      for (const name of categoriesOf(entry)) counts.set(name, (counts.get(name) ?? 0) + 1);
    }
    const kinds: readonly PromptKind[] = PROMPT_KINDS;
    expect(kinds).toHaveLength(9);
    for (const kind of kinds) expect(counts.get(`kind:${kind}`) ?? 0, `kind ${kind}`).toBeGreaterThanOrEqual(3);
    expect(counts.get("no_label") ?? 0, "no label").toBeGreaterThanOrEqual(5);
    for (const name of ["option_ids", "question", "request", "etc_shield", "duration_vs_platform", "non_ascii", "emoji"]) {
      expect(counts.get(name) ?? 0, name).toBeGreaterThanOrEqual(1);
    }
  });

  it("holds Spanish and English hand phrasings and the stub's own turns", () => {
    const sources = new Set(entries.map((e) => e.source));
    expect(sources).toEqual(new Set(["hand", "stub"]));
    expect(corpus.runs.length).toBeGreaterThan(0);
  });
});

describe("normalize", () => {
  it("strips accents and lower-cases", () => {
    expect(normalize("ÁÉÍÓÚ Ñandú ÜBER")).toBe("aeiou nandu uber");
    expect(normalize("¿Cuál es tu ZONA?")).toBe("¿cual es tu zona?");
    expect(normalize("İ")).toBe("i");
  });

  it("leaves letters without a decomposition alone", () => {
    expect(normalize("ß")).toBe("ß");
  });
});

describe("asksSomething", () => {
  it("is true for a question mark, an inverted one, or interactive options", () => {
    expect(asksSomething("¿Tu nombre")).toBe(true);
    expect(asksSomething("Your name?")).toBe(true);
    expect(asksSomething("Gracias.")).toBe(false);
    expect(asksSomething("Gracias.", { options: [{ id: "yes" }] })).toBe(true);
    expect(asksSomething("Gracias.", { options: [] })).toBe(true);
  });

  it("treats an empty or missing interactive block as none, as Python does", () => {
    expect(asksSomething("Gracias.", null)).toBe(false);
    expect(asksSomething("Gracias.", undefined)).toBe(false);
    expect(asksSomething("Gracias.", {})).toBe(false);
  });
});

describe("zoneOptions", () => {
  it("returns the options only when every one is a Zone id", () => {
    const zones = { options: [{ id: "zone_a", label: "A" }, { id: "zone_b", label: "B" }] };
    expect(zoneOptions(zones)).toEqual(zones.options);
    expect(zoneOptions({ options: [{ id: "zone_a" }, { id: "x" }] })).toEqual([]);
    expect(zoneOptions({ options: [] })).toEqual([]);
    expect(zoneOptions({})).toEqual([]);
    expect(zoneOptions(null)).toEqual([]);
    expect(zoneOptions(undefined)).toEqual([]);
  });
});

describe("lastQuestion", () => {
  it("returns the last sentence that ends in a question mark", () => {
    expect(lastQuestion("¿Cuál es tu zona?")).toBe("Cuál es tu zona?");
    expect(lastQuestion("Hola¿Qué tal? ¿Y tu zona?")).toBe("Y tu zona?");
    expect(lastQuestion("Línea\n¿Tu nombre?")).toBe("Tu nombre?");
    expect(lastQuestion("Thanks. Nothing else.")).toBeNull();
    expect(lastQuestion("")).toBeNull();
  });

  it("ignores a question mark that stands alone", () => {
    expect(lastQuestion("?")).toBeNull();
    expect(lastQuestion("¿¿??")).toBeNull();
  });

  it("does not end a sentence at etc., e.g., i.e., Tel., Ej. or an ellipsis", () => {
    expect(lastQuestion("Which apps (Glovo, Uber Eats, etc.) have you used?")).toBe("Which apps (Glovo, Uber Eats, etc.) have you used?");
    expect(lastQuestion("e.g. glovo? i.e. rappi?")).toBe("i.e. rappi?");
    expect(lastQuestion("Tel. 555. ¿Tu nombre?")).toBe("Tu nombre?");
    expect(lastQuestion("Wait... ¿Cuándo empiezas?")).toBe("Cuándo empiezas?");
    expect(lastQuestion("Ej. ¿Cómo?")).toBe("Cómo?");
  });
});

describe("lastRequest", () => {
  it("returns the last sentence that asks in a statement or imperative", () => {
    expect(lastRequest("Necesito tu nombre.")).toBe("Necesito tu nombre");
    expect(lastRequest("Dime tu nombre. Gracias.")).toBe("Dime tu nombre");
    expect(lastRequest("Por favor, indica tu zona")).toBe("Por favor, indica tu zona");
  });

  it("returns null for a plain statement and for a question", () => {
    expect(lastRequest("Thanks. Nothing else.")).toBeNull();
    expect(lastRequest("¿Cuál es tu zona?")).toBeNull();
    expect(lastRequest("")).toBeNull();
  });
});

describe("detectPrompt", () => {
  it("reads the option ids first", () => {
    const ids = (...list: string[]): Interactive => ({ options: list.map((id) => ({ id })) });
    expect(detectPrompt("hola", ids("full_time"))).toBe("availability");
    expect(detectPrompt("hola", ids("full_time", "morning"))).toBeNull();
    expect(detectPrompt("hola", ids("morning", "flexible"))).toBe("preferred_schedule");
    expect(detectPrompt("hola", ids("glovo", "none"))).toBe("experience_platforms");
    expect(detectPrompt("hola", ids("2026-10-12", "2026-10-13"))).toBe("start_date");
    expect(detectPrompt("hola", ids("2026-10-12", "x"))).toBeNull();
    expect(detectPrompt("hola", ids("zone_1"))).toBe("zone");
    expect(detectPrompt("hola", ids("yes", "no"))).toBeNull();
    expect(detectPrompt("hola", ids("2026-10-12\n"))).toBe("start_date");
    expect(detectPrompt("hola", { options: [] })).toBeNull();
    expect(detectPrompt("hola", {})).toBeNull();
    expect(detectPrompt("hola", null)).toBeNull();
    expect(detectPrompt("hola")).toBeNull();
  });

  it("prefers the options over the text, and the question over the request", () => {
    expect(detectPrompt("¿Cuál es tu nombre?", { options: [{ id: "zone_1" }] })).toBe("zone");
    expect(detectPrompt("Dime tu zona. ¿Cuál es tu nombre?")).toBe("full_name");
  });

  it("falls back to a request when the question names no topic", () => {
    expect(detectPrompt("¿Me ayudas con un dato más? Necesito tu zona.")).toBe("zone");
    expect(detectPrompt("Por favor, indica tu zona")).toBe("zone");
  });

  it("answers the years question when it names apps in passing, the platforms one when it asks which", () => {
    expect(detectPrompt("How many years on delivery apps?")).toBe("experience_years");
    expect(detectPrompt("Which apps (Glovo, Uber Eats, etc.) have you used?")).toBe("experience_platforms");
    expect(detectPrompt("What apps do you use? Also how long?")).toBe("experience_years");
    expect(detectPrompt("Which apps have you used, and for how many years?")).toBe("experience_platforms");
  });

  it("matches words with Python's Unicode word boundaries, not JavaScript's ASCII ones", () => {
    // "ß" and "ł" survive normalisation. In Python they are word characters, so "right" and
    // "zona" glued to them are not whole words; JavaScript's ASCII \b would find a boundary.
    expect(detectPrompt("¿ßright?")).toBeNull();
    expect(detectPrompt("¿Tu łzona?")).toBeNull();
    expect(detectPrompt("¿Cuál es tu ñzona?")).toBeNull();
    expect(detectPrompt("¿Cuál es tu zona?")).toBe("zone");
  });

  it("finds nothing in a statement, an empty text or a topic-less question", () => {
    expect(detectPrompt("Thanks. Nothing else.")).toBeNull();
    expect(detectPrompt("")).toBeNull();
    expect(detectPrompt("Ej. ¿Cómo?")).toBeNull();
  });
});
