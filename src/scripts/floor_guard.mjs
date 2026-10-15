#!/usr/bin/env node
// floor_guard.mjs — diff-scoped enforcement of the CONSTRAINTS.md floor.
// Adapted from the constraint-driven-development skill's reference (references/floor-guard.md):
// same contract, Criba patterns. Runs in the dev container via src/scripts/check.sh.
// Usage: node src/scripts/floor_guard.mjs [--base <ref>] [--only <path>]...
//   --base  default main (no remote)
//   --only  report only findings in these files (the edit-loop hook); the whole diff is still read
// Exit: 0 clean, 1 at least one floor violation, 2 the guard could not run.
// An edited assertion is assertion-changed (printed, not failing), a reformatted one is not reported; see "2b" below.
import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';

const argValues = (name) => process.argv.flatMap((a, i) => (a === name ? [process.argv[i + 1]] : []));
const base = argValues('--base')[0] ?? 'main';
const only = new Set(argValues('--only'));

// `git diff --no-index` exits 1 whenever the two sides differ, which is the normal case for a
// new file, so that output is kept. Any other failure is null, and null never reads as clean.
// One retry, only for a loose object the dev container could not open yet (Docker file sharing,
// under host load; check.sh packs fresh objects first, this is the second line of defence).
const looseObjectDenied = /unable to open loose object [0-9a-f]+: Permission denied/;
const git = (args, { diffExit = false } = {}, retried = false) => {
  try { return execFileSync('git', args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], maxBuffer: 64 << 20 }); }
  catch (e) {
    if (diffExit && e.status === 1 && typeof e.stdout === 'string') return e.stdout;
    if (!retried && looseObjectDenied.test(String(e.stderr ?? ''))) {
      console.error('floor-guard: git could not open a loose object, retrying once: git ' + args.join(' '));
      return git(args, { diffExit }, true);
    }
    return null;
  }
};
const bail = (msg) => { console.error('floor-guard: ' + msg); process.exit(2); };

const top = git(['rev-parse', '--show-toplevel'])?.trim();
if (!top) bail('not inside a git work tree');
process.chdir(top);

const mergeBase = git(['merge-base', base, 'HEAD'])?.trim();
if (!mergeBase) bail('no merge base against ' + base);

// Unified diff plus untracked files (git diff alone cannot see new files).
const tracked = git(['diff', '--unified=0', mergeBase, '--']);
if (tracked === null) bail('could not diff against ' + mergeBase);
const untrackedFiles = git(['ls-files', '--others', '--exclude-standard']);
if (untrackedFiles === null) bail('could not list untracked files');
const untracked = untrackedFiles.split('\n').filter(Boolean).map((f) => {
  const d = git(['diff', '--no-index', '--unified=0', '/dev/null', f], { diffExit: true });
  if (d === null) bail('could not diff untracked file ' + f);
  return d;
}).join('\n');
const diff = tracked + '\n' + untracked;

// .constraintsignore: one glob per line (`**` any depth, `*` within a segment), `#` comments.
const globToRe = (g) => new RegExp('^' + g.split('**').map((part) =>
  part.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '[^/]*').replace(/\?/g, '[^/]')).join('.*') + '$');
const ignored = existsSync('.constraintsignore')
  ? readFileSync('.constraintsignore', 'utf8').split('\n').map((l) => l.trim()).filter((l) => l && !l.startsWith('#')).map(globToRe)
  : [];
const isIgnored = (f) => ignored.some((re) => re.test(f));

// Exception IDs in CONSTRAINTS.md. A suppression that cites one (`exception W1`) is tracked, not silent.
const constraintsText = existsSync('CONSTRAINTS.md') ? readFileSync('CONSTRAINTS.md', 'utf8') : '';
const exceptionIds = new Set([...constraintsText.matchAll(/^\| *([WE]\d+) *\|/gm)].map((m) => m[1]));
const citesException = (t) => [...t.matchAll(/\bexception ([WE]\d+)\b/g)].some((m) => exceptionIds.has(m[1]));

// Path-scoped exceptions, for exactly two findings. A row lifts a finding only if its Rule column
// names the finding id, its Path column has a glob matching the finding's file, and its Expires
// date (UTC) is today or later. An expired or undated row lifts nothing and is reported itself.
const LIFTABLE = ['assertion-removed', 'test-deleted'];
const todayUtc = new Date().toISOString().slice(0, 10);
const exceptionRows = constraintsText.split('\n').flatMap((line) => {
  if (!/^\| *[WE]\d+ *\|/.test(line)) return [];
  const cells = line.split('|').slice(1, -1).map((c) => c.trim());
  if (cells.length < 6) return [];
  const [id, rule, path, , , expires] = cells;
  const ticked = [...path.matchAll(/`([^`]+)`/g)].map((m) => m[1]);
  const globs = (ticked.length ? ticked : path.split(',')).map((g) => g.trim()).filter(Boolean);
  const dated = /^\d{4}-\d{2}-\d{2}$/.test(expires) && !Number.isNaN(Date.parse(expires));
  return [{
    id, line, expires,
    expired: !dated || expires < todayUtc,
    names: LIFTABLE.filter((r) => new RegExp('(?<![\\w-])' + r + '(?![\\w-])').test(rule)),
    globs: globs.map((g) => globToRe(g)),
  }];
});
const liftingRow = (rule, f) => exceptionRows.find((r) =>
  !r.expired && r.names.includes(rule) && r.globs.some((re) => re.test(f)));

// Walk the diff. `---` and `+++` are file headers only between a file's `diff` line and its first
// `@@` hunk; inside a hunk every line is content. Both headers name the file, so a deletion
// (`+++ /dev/null`) keeps its name.
const added = [], removed = [], deleted = [];
const pathOf = (s) => s.replace(/^[ab]\//, '');
let file = '', oldFile = '', inHeader = false, oldLn = 0, newLn = 0;
for (const line of diff.split('\n')) {
  if (line.startsWith('diff ')) inHeader = true;
  else if (line.startsWith('@@')) {
    inHeader = false;
    const m = /^@@ -(\d+)(?:,\d+)? \+(\d+)/.exec(line);
    oldLn = m ? Number(m[1]) : 0;
    newLn = m ? Number(m[2]) : 0;
  }
  else if (inHeader) {
    if (line.startsWith('--- ')) oldFile = pathOf(line.slice(4));
    else if (line.startsWith('+++ ')) {
      const newFile = pathOf(line.slice(4));
      file = newFile === '/dev/null' ? oldFile : newFile;
      if (newFile === '/dev/null') deleted.push(file);
    }
  }
  else if (line.startsWith('+')) added.push({ file, text: line.slice(1), line: newLn++ });
  else if (line.startsWith('-')) removed.push({ file, oldFile, text: line.slice(1), line: oldLn++ });
}

const findings = [];
const lifted = new Map();   // "E1 rule file" -> { id, rule, file, count }
const flag = (rule, f, text) => {
  if (only.size === 0 || only.has(f)) findings.push({ rule, file: f, text: text.trim().slice(0, 120) });
};
// Like flag, but a matching unexpired exception row lifts the finding: reported, not failed.
const flagUnlessExcepted = (rule, f, text) => {
  const row = liftingRow(rule, f);
  if (!row) return flag(rule, f, text);
  if (only.size !== 0 && !only.has(f)) return;
  const key = row.id + ' ' + rule + ' ' + f;
  const entry = lifted.get(key) ?? { id: row.id, rule, file: f, count: 0 };
  entry.count += 1;
  lifted.set(key, entry);
};
const isCode = (f) => /\.(py|[cm]?[jt]sx?|sh)$|(^|\/)Dockerfile$/.test(f);
const isTest = (f) => /\.(test|spec)\.|_test\.|(^|\/)test_|(^|\/)conftest\.py$/.test(f);
const isConstraints = (f) => /(^|\/)CONSTRAINTS\.md$/.test(f);

// 1. Silenced checker.
const SUPPRESSIONS = /@ts-ignore|@ts-nocheck|@ts-expect-error|eslint-disable|biome-ignore|# *noqa|# *type: *ignore|# *pragma: *no cover|istanbul ignore|v8 ignore|nosemgrep|gitleaks:allow|Stryker disable/;
// 4. Unfinished work.
const STUBS = /throw new (Error|NotImplemented).*[Nn]ot implemented|catch\s*\(\w*\)\s*\{\s*\}|catch\s*\{\s*\}|\bTODO\b|\bFIXME\b|raise NotImplementedError|\bexcept\b[^:]*:\s*pass\s*$|\bpass\s*# *stub/;
// 2. A test made easier (added skips).
const SKIPS = /\.(skip|todo)\b|\bxit\(|\bxdescribe\(|@pytest\.mark\.(skip|xfail)|\bpytest\.skip\(/;

for (const { file, text } of added) {
  if (isIgnored(file)) continue;
  if (isCode(file)) {
    if (SUPPRESSIONS.test(text) && !citesException(text)) flag('silenced-checker', file, text);
    if (STUBS.test(text)) flag('unfinished-work', file, text);
    if (SKIPS.test(text)) flag('test-made-easier', file, text);
  }
  if (isConstraints(file) && /^\| *(W|E)\d+ *\|/.test(text)) flag('new-exception', file, text);
}

// 2b. A test file deleted, or an assertion removed from a test file that still exists.
// Assertions are compared as whole statements, not lines: a multi-line `assert (...)` or `expect(...)`
// chain is read from its first line to its last, and a pure re-wrap or reformat (whitespace, line
// breaks, quote style, trailing commas, the parentheses a formatter puts round an assert condition)
// leaves the same canonical text, so it is no finding at all.
// An edited assertion is not a removed one: a removed assertion statement pairs with an added
// assertion statement in the same test file, in the same enclosing function or it/test block, about
// the same subject (the left-hand side of the comparison, or the first argument). A pair is reported
// as assertion-changed and does not fail. A removal left without a partner is a net loss in that
// block and stays assertion-removed, so exception rows still apply to it.
const ASSERTION = /\b(expect|assert|should)\b/;
const normalise = (t) => t.replace(/\s+/g, ' ').trim();

// Index of the bracket closing the one at s[open], or -1. Skips string literals.
const closeOf = (s, open) => {
  let depth = 0, quote = '';
  for (let i = open; i < s.length; i++) {
    const c = s[i];
    if (quote) { if (c === '\\') i++; else if (c === quote) quote = ''; continue; }
    if (c === '"' || c === "'" || c === '`') quote = c;
    else if ('([{'.includes(c)) depth++;
    else if (')]}'.includes(c) && --depth === 0) return i;
  }
  return -1;
};
// The part of s before its first top-level comparison, `in`/`is`, or comma; null when brackets or
// quotes are left open (a multi-line assertion).
const STOP = /^(?:==|!=|<=|>=|<|>|,|\s+(?:not\s+in|in|is\s+not|is)\s)/;
const lhsOf = (s) => {
  const t = s.trim();
  if (t.startsWith('(')) {
    const c = closeOf(t, 0);
    if (c === t.length - 1 || (c > 0 && /^\s*,/.test(t.slice(c + 1)))) return lhsOf(t.slice(1, c));
  }
  let depth = 0, quote = '';
  for (let i = 0; i < t.length; i++) {
    const c = t[i];
    if (quote) { if (c === '\\') i++; else if (c === quote) quote = ''; continue; }
    if (c === '"' || c === "'" || c === '`') quote = c;
    else if ('([{'.includes(c)) depth++;
    else if (')]}'.includes(c)) depth--;
    else if (depth === 0 && STOP.test(t.slice(i))) return t.slice(0, i);
  }
  return depth === 0 && !quote ? t : null;
};
// The normalised subject of an assertion line, or null when it cannot be told (comments, bare
// multi-line openers): such a line never pairs, so it keeps the unpaired behaviour.
const subjectOf = (text) => {
  const t = text.trim();
  let m, subject = null;
  if ((m = /^assert\s+(.*)$/.exec(t))) subject = lhsOf(m[1]);
  else if ((m = /\bexpect\s*\(/.exec(t))) {
    const close = closeOf(t, m.index + m[0].length - 1);
    subject = close === -1 ? null : t.slice(m.index + m[0].length, close);
  } else if ((m = /\.should\b/.exec(t))) subject = t.slice(0, m.index);
  else if ((m = /\b(?:assert|should)\b(?:\.\w+)*\s*\(/.exec(t))) {
    const open = m.index + m[0].length - 1, close = closeOf(t, open);
    subject = close === -1 ? null : lhsOf(t.slice(open + 1, close));
  }
  const n = subject === null ? '' : normalise(subject).replace(/\s*([^\w\s])\s*/g, '$1');
  return n === '' ? null : n;
};

// The enclosing function / class / it / test / describe chain of line index idx, by indentation:
// walk up; each line indented less than everything seen so far that opens a block adds its name.
// Closers (`)`, `]`, `}`) and comments are skipped. null when the file could not be read.
const indentOf = (l) => l.replace(/\t/g, '    ').search(/\S/);
const quoted = (l) => /(["'`])((?:\\.|(?!\1).)+)\1/.exec(l)?.[2];
const openerName = (lines, i) => {
  const l = lines[i];
  let m;
  if ((m = /^\s*(?:async\s+)?def\s+(\w+)/.exec(l)) || (m = /^\s*class\s+(\w+)/.exec(l))) return m[1];
  if ((m = /^\s*(?:export\s+)?(?:async\s+)?function\s*\*?\s*(\w+)/.exec(l))) return m[1];
  if ((m = /^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?(?:\(|function\b|\w+\s*=>)/.exec(l))) return m[1];
  if ((m = /^\s*(beforeEach|afterEach|beforeAll|afterAll)\s*\(/.exec(l))) return m[1];
  if ((m = /^\s*(?:await\s+)?(describe|it|test)\b(?:\.\w+)*\s*[(`]/.exec(l))) {
    const title = [0, 1, 2, 3].map((k) => quoted(lines[i + k] ?? '')).find(Boolean);
    return m[1] + (title ? ' ' + title : '');
  }
  return null;
};
const blockOf = (lines, idx) => {
  if (!lines || idx < 0 || idx >= lines.length) return null;
  const chain = [];
  let cur = indentOf(lines[idx]);
  if (cur < 0) return null;
  for (let i = idx - 1; i >= 0 && cur > 0; i--) {
    const ind = indentOf(lines[i]);
    if (ind < 0 || ind >= cur || /^\s*(?:[)\]}]|#|\/\/)/.test(lines[i])) continue;
    cur = ind;
    const name = openerName(lines, i);
    if (name) chain.unshift(name);
  }
  return chain.join(' > ');
};

// Assertion statements. A statement that starts with `assert` / `expect(` / `.should` is read whole,
// across its bracketed (or backslash, or leading-dot) continuation lines, into tokens; its canonical
// form drops comments, whitespace, line wrapping, quote style, trailing commas and the parentheses a
// formatter wraps around an `assert` condition or message. Two statements with the same canonical
// text are the same statement, however they are wrapped. Any other line that mentions an assertion
// word stays a one-line unit, compared by its whitespace-normalised text.
const HEAD = /^(?:(?:await|return|yield)\s+)?(?:assert\b|expect\s*[(.]|[\w$.\])'"]*\.should\b)/;
const MAX_STATEMENT_LINES = 200;
const PREFIX = /^[rRbBuUfF]{1,2}$/;
const KEYWORDS = new Set(['assert', 'in', 'not', 'and', 'or', 'is', 'if', 'else', 'elif', 'return', 'yield', 'await', 'lambda', 'for', 'while']);
const wordish = (t) => t.t === 'w' || t.t === 'q';

// A string token: v is its text with the quotes normalised to double quotes where that changes
// nothing (so '...' and "..." compare equal); body is set only for plain strings, which may merge.
const stringToken = (pre, raw) => {
  const prefix = pre.toLowerCase();
  const q = raw[0];
  if (raw.length < 2 || !raw.endsWith(q) || raw.startsWith(q.repeat(3)) || q === '`') return { t: 'q', v: prefix + raw };
  const body = raw.slice(1, -1);
  if (prefix.includes('r')) return { t: 'q', v: body.includes('"') ? prefix + raw : prefix + '"' + body + '"' };
  const plain = body.replace(/\\(['"])/g, '$1').replace(/"/g, '\\"');
  return { t: 'q', v: prefix + '"' + plain + '"', pre: prefix, body: plain };
};

// Tokens of the statement that starts at lines[i]: { end, toks }. Comments are dropped; each token
// carries sp (whitespace came before it). A statement ends on the first line where its brackets are
// closed, unless the line ends in a backslash (Python) or the next line starts with `.` (JS chain).
// One that never closes is read as its first line alone.
const scanStatement = (lines, i, py) => {
  const run = (limit) => {
    const toks = [];
    let depth = 0, quote = '', buf = '', pre = '', block = false, word = '', sp = false;
    const push = (tok) => { toks.push({ ...tok, sp }); sp = false; };
    const flushWord = () => { if (word) { push({ t: 'w', v: word }); word = ''; } };
    for (let n = i; n < lines.length; n++) {
      const s = lines[n];
      let backslash = false;
      if (n > i) { if (quote) buf += '\n'; else { flushWord(); sp = true; } }
      for (let k = 0; k < s.length; k++) {
        const c = s[k];
        if (block) { if (c === '*' && s[k + 1] === '/') { block = false; k++; } continue; }
        if (quote) {
          if (c === '\\') { buf += c; if (k + 1 < s.length) buf += s[++k]; }
          else if (s.startsWith(quote, k)) { buf += quote; k += quote.length - 1; push(stringToken(pre, buf)); quote = ''; }
          else buf += c;
          continue;
        }
        if (py ? c === '#' : c === '/' && s[k + 1] === '/') break;
        if (!py && c === '/' && s[k + 1] === '*') { block = true; k++; continue; }
        if (py && c === '\\' && s.slice(k + 1).trim() === '') { backslash = true; break; }
        if (c === '"' || c === "'" || (!py && c === '`')) {
          pre = PREFIX.test(word) ? word : '';
          if (pre) word = '';
          flushWord();
          quote = py && s.startsWith(c.repeat(3), k) ? c.repeat(3) : c;
          buf = quote;
          k += quote.length - 1;
          continue;
        }
        if (/\s/.test(c)) { flushWord(); sp = true; continue; }
        if (/[\w$]/.test(c)) { word += c; continue; }
        flushWord();
        if ('([{'.includes(c)) depth++;
        else if (')]}'.includes(c)) depth--;
        const last = toks.at(-1);
        if (!sp && '=!<>'.includes(c) && last?.t === 'p' && /^[=!<>]+$/.test(last.v)) last.v += c;   // ==, !=, <=, >=
        else push({ t: 'p', v: c });
      }
      // A one-line string left open without a backslash continuation is malformed: close it here.
      if (quote.length === 1 && quote !== '`' && !/\\$/.test(s)) { push(stringToken(pre, buf + quote)); quote = ''; }
      if (!quote) flushWord();
      if (quote || block) { if (n - i + 1 >= limit) return null; continue; }
      const next = (lines[n + 1] ?? '').trim();
      const chain = !py && /^\??\.(?!\.\.)/.test(next);
      if ((depth <= 0 && !backslash && !chain) || n === lines.length - 1) return { end: n, toks };
      if (n - i + 1 >= limit) return null;
    }
    return null;
  };
  return run(MAX_STATEMENT_LINES) ?? run(1) ?? { end: i, toks: [] };
};

// Index of the bracket token closing the one at toks[open], or -1.
const matchOf = (toks, open) => {
  let depth = 0;
  for (let k = open; k < toks.length; k++) {
    if (toks[k].t !== 'p') continue;
    if ('([{'.includes(toks[k].v)) depth++;
    else if (')]}'.includes(toks[k].v) && --depth === 0) return k;
  }
  return -1;
};
const topLevel = (toks, test) => {
  let depth = 0;
  return toks.some((t) => {
    if (t.t === 'p' && '([{'.includes(t.v)) depth++;
    else if (t.t === 'p' && ')]}'.includes(t.v)) depth--;
    return depth === 0 && test(t);
  });
};
// Parentheses a formatter wraps around the whole condition or the whole message of an `assert`.
const unwrapSlot = (slot) => {
  for (;;) {
    const m = slot.length >= 2 && slot[0].t === 'p' && slot[0].v === '(' ? matchOf(slot, 0) : -1;
    if (m !== slot.length - 1) return slot;
    const inner = slot.slice(1, -1);
    const walrus = inner.some((t, k) => t.v === ':' && inner[k + 1]?.v === '=');
    if (walrus || topLevel(inner, (t) => t.v === ',' || (t.t === 'w' && (t.v === 'for' || t.v === 'yield')))) return slot;
    slot = inner;
  }
};
// `assert <cond>, <message>` -> { cond, msg } (msg is null without a top-level comma); toks[0] is `assert`.
const splitAssert = (toks) => {
  let depth = 0, comma = -1;
  for (let k = 1; k < toks.length && comma === -1; k++) {
    if (toks[k].t !== 'p') continue;
    if ('([{'.includes(toks[k].v)) depth++;
    else if (')]}'.includes(toks[k].v)) depth--;
    else if (toks[k].v === ',' && depth === 0) comma = k;
  }
  return comma === -1 ? { cond: toks.slice(1), msg: null } : { cond: toks.slice(1, comma), msg: toks.slice(comma + 1) };
};
const joinTokens = (toks) => toks.map((t, k) => (k > 0 && wordish(t) && wordish(toks[k - 1]) ? ' ' : '') + t.v).join('');
// A condition that is one `a == b` / `a != b` and nothing else compares the same either way round
// (ruff's yoda-condition fix turns `("zone", x) == got` into `got == ("zone", x)`): order the sides.
const orderedEquality = (cond) => {
  const ops = [];
  let depth = 0;
  cond.forEach((t, k) => {
    if (t.t === 'p' && '([{'.includes(t.v)) depth++;
    else if (t.t === 'p' && ')]}'.includes(t.v)) depth--;
    else if (depth === 0 && (t.t === 'p' ? /^(?:[=!<>]=?|:|\?)$/.test(t.v) : /^(?:and|or|not|in|is|if|else|lambda|for|yield)$/.test(t.v))) ops.push(k);
  });
  if (ops.length !== 1 || !['==', '!='].includes(cond[ops[0]].v)) return cond;
  const left = cond.slice(0, ops[0]), right = cond.slice(ops[0] + 1);
  return joinTokens(left) <= joinTokens(right) ? cond : [...right, cond[ops[0]], ...left];
};
// Drop trailing commas before a closing bracket, except the one that makes a one-element tuple
// `(a,)` or subscript `x[a,]` in Python.
const dropTrailingCommas = (toks, py) => {
  const stack = [], commas = new Map(), openOf = new Map();
  toks.forEach((t, k) => {
    if (t.t !== 'p') return;
    if ('([{'.includes(t.v)) { stack.push(k); commas.set(k, 0); }
    else if (')]}'.includes(t.v)) openOf.set(k, stack.pop());
    else if (t.v === ',' && stack.length) commas.set(stack.at(-1), commas.get(stack.at(-1)) + 1);
  });
  return toks.filter((t, k) => {
    const close = toks[k + 1];
    if (!(t.t === 'p' && t.v === ',' && close?.t === 'p' && ')]}'.includes(close.v))) return true;
    const open = openOf.get(k + 1), before = toks[open - 1];
    const after = before && ((before.t === 'w' && !KEYWORDS.has(before.v)) || before.t === 'q' || (before.t === 'p' && ')]'.includes(before.v)));
    return py && commas.get(open) === 1 && ((close.v === ')' && !after) || (close.v === ']' && after));
  });
};
// Canonical form of a statement's tokens: { canon, view }. canon is the text two statements share
// when they differ only in layout; view is the same statement as one readable line (what the
// report prints and the subject is read from).
const canonicalOf = (toks, py) => {
  let out = toks;
  if (py) {
    out = out.reduce((acc, t) => {   // implicit concatenation "a" "b" is the one string "ab"
      const last = acc.at(-1);
      if (t.body !== undefined && last?.body !== undefined && last.pre === t.pre) {
        acc[acc.length - 1] = { t: 'q', v: t.pre + '"' + last.body + t.body + '"', pre: t.pre, body: last.body + t.body, sp: last.sp };
      } else acc.push(t);
      return acc;
    }, []);
  }
  const isAssert = py && out[0]?.t === 'w' && out[0].v === 'assert';
  if (isAssert) {
    const { cond, msg } = splitAssert(out);
    out = [out[0], ...unwrapSlot(cond), ...(msg ? [out[cond.length + 1], ...unwrapSlot(msg)] : [])];
  }
  out = dropTrailingCommas(out, py);
  const view = codeOf(out.map((t, k) => (k === 1 && isAssert ? { ...t, sp: true } : t)));
  if (isAssert) {
    const { cond, msg } = splitAssert(out);
    out = [out[0], ...orderedEquality(cond), ...(msg ? [{ t: 'p', v: ',' }, ...msg] : [])];
  }
  return { canon: joinTokens(out.filter((t) => !(t.t === 'p' && t.v === ';'))), view };
};
// The statement's code as one line (comments gone, quotes normalised), for display and subjects.
const codeOf = (toks) => toks.map((t, k) => {
  const prev = toks[k - 1];
  const tight = (t.t === 'p' && ')]}'.includes(t.v)) || (prev?.t === 'p' && '([{'.includes(prev.v));
  return (k > 0 && ((t.sp && !tight) || (wordish(t) && wordish(prev))) ? ' ' : '') + t.v;
}).join('');

// File contents on each side of the diff: the merge base blob and the working tree.
const memo = new Map();
const linesOf = (key, load) => {
  if (!memo.has(key)) { const t = load(); memo.set(key, t === null ? null : t.split('\n')); }
  return memo.get(key);
};
const oldLinesOf = (f) => linesOf('old:' + f, () => git(['show', mergeBase + ':' + f]));
const newLinesOf = (f) => linesOf('new:' + f, () => (existsSync(f) ? readFileSync(f, 'utf8') : null));

// The assertion units of one file version, as lineUnit[idx] -> { start, end, block, canon, subject,
// code, whole } for every line idx a unit covers. A statement that starts with an assertion word is
// one unit; any other line that mentions one is a unit of its own, and in Python it points at its
// whole logical statement (whole), so that a formatter re-wrapping that statement can cancel it.
// Memoised per version.
const unitsOf = (key, lines, f) => {
  if (!memo.has('units:' + key)) {
    const py = /\.py$/.test(f), lineUnit = new Array(lines.length);
    const place = (unit) => {
      unit.block = blockOf(lines, unit.start);
      for (let k = unit.start; k <= unit.end; k++) lineUnit[k] = unit;
    };
    const oneLine = (k, whole) => {
      const text = lines[k].trim();
      place({ start: k, end: k, canon: normalise(text).replace(/,$/, ''), subject: subjectOf(lines[k]), code: text, whole });
    };
    for (let i = 0; i < lines.length; i++) {
      const trimmed = lines[i].trim();
      if (trimmed === '' || (!py && !HEAD.test(trimmed) && !ASSERTION.test(lines[i]))) continue;
      if (!py && !HEAD.test(trimmed)) { oneLine(i); continue; }
      const { end, toks } = scanStatement(lines, i, py);
      if (HEAD.test(trimmed)) {
        const { canon, view } = canonicalOf(toks, py);
        place({ start: i, end, canon, subject: subjectOf(view), code: view });
      } else {
        const hits = [];
        for (let k = i; k <= end; k++) if (ASSERTION.test(lines[k])) hits.push(k);
        const whole = hits.length && toks.length ? { canon: canonicalOf(toks, py).canon, block: blockOf(lines, i) } : undefined;
        for (const k of hits) oneLine(k, whole);
      }
      i = end;
    }
    memo.set('units:' + key, lineUnit);
  }
  return memo.get('units:' + key);
};
// The distinct units touched by these diff lines (1-based line numbers), in order.
const touchedUnits = (key, lines, f, lineNumbers) => {
  if (!lines) return [];
  const lineUnit = unitsOf(key, lines, f);
  return [...new Set(lineNumbers.map((n) => lineUnit[n - 1]).filter(Boolean))];
};

const inScope = (f) => isTest(f) && !deleted.includes(f) && !isIgnored(f);
const scopedFiles = new Map();   // file -> { oldFile, removed: [line numbers], added: [line numbers] }
const touch = (side, l) => {
  if (!inScope(l.file)) return;
  const entry = scopedFiles.get(l.file) ?? { oldFile: l.file, removed: [], added: [] };
  if (side === 'removed') entry.oldFile = l.oldFile || l.file;
  entry[side].push(l.line);
  scopedFiles.set(l.file, entry);
};
for (const a of added) touch('added', a);
for (const r of removed) touch('removed', r);

const changed = [];
for (const f of deleted) if (isTest(f) && !isIgnored(f)) flagUnlessExcepted('test-deleted', f, 'file deleted');
for (const [f, { oldFile, removed: gone, added: came }] of scopedFiles) {
  const out = touchedUnits('old:' + oldFile, oldLinesOf(oldFile), oldFile, gone);
  const into = touchedUnits('new:' + f, newLinesOf(f), f, came);
  // A removed statement with an added one of the same canonical text in the same block is
  // unchanged (a re-wrap, a reorder): no finding. Counted, so two removed and one added is a net loss.
  const whole = (u) => u.whole ?? u;
  const identical = new Map();
  for (const w of new Set(into.map(whole))) {
    if (w.block !== null) identical.set(w.block + '\0' + w.canon, [...(identical.get(w.block + '\0' + w.canon) ?? []), w]);
  }
  const cancelledOut = new Set(), cancelledInto = new Set();
  for (const w of new Set(out.map(whole))) {
    const partner = w.block === null ? undefined : identical.get(w.block + '\0' + w.canon)?.shift();
    if (partner) { cancelledOut.add(w); cancelledInto.add(partner); }
  }
  const unmatched = out.filter((u) => !cancelledOut.has(whole(u)));
  const pool = new Map();   // "block subject" -> added statements not yet paired
  for (const u of into) {
    if (cancelledInto.has(whole(u)) || u.subject === null || u.block === null) continue;
    const key = u.block + '\0' + u.subject;
    pool.set(key, [...(pool.get(key) ?? []), u]);
  }
  for (const u of unmatched) {
    const partner = u.subject === null || u.block === null ? undefined : pool.get(u.block + '\0' + u.subject)?.shift();
    if (partner) changed.push({ file: f, block: u.block, from: u.code, to: partner.code });
    else flagUnlessExcepted('assertion-removed', f, u.code);
  }
}

// 1b/2c. A rule in CONSTRAINTS.md weakened or removed (unchanged from the reference).
const ruleKey = (t) => {
  const s = t.trim();
  if (s.startsWith('|')) return s.split('|').map((c) => c.trim()).filter(Boolean)[0] ?? '';
  if (/^[-*] /.test(s)) return s.slice(2).split(':')[0].trim();
  return null;
};
const isException = (t) => /^\| *(W|E)\d+ *\|/.test(t.trim());
const MIN_BEFORE = /(>=|>|≥|at least|minimum|\bmin\b|no less than|not fall|not drop)\s*$/;
const MAX_BEFORE = /(<=|<|≤|at most|maximum|\bmax\b|no more than|under|below|not grow|not exceed)\s*$/;
const MIN_AFTER = /^\s*\S*\s*(or more|or higher|must not fall|must not drop)/;
const MAX_AFTER = /^\s*\S*\s*(or less|or lower|must not grow|must not exceed)/;
const thresholds = (t) => {
  const out = [], re = /\d+(?:\.\d+)?/g;
  let m;
  while ((m = re.exec(t))) {
    const before = t.slice(Math.max(0, m.index - 24), m.index).toLowerCase();
    const after = t.slice(m.index + m[0].length, m.index + m[0].length + 40).toLowerCase();
    const dir = MIN_BEFORE.test(before) || MIN_AFTER.test(after) ? 'min'
      : MAX_BEFORE.test(before) || MAX_AFTER.test(after) ? 'max' : null;
    out.push({ n: Number(m[0]), dir });
  }
  return out;
};
const removedRules = removed.filter((l) => isConstraints(l.file) && ruleKey(l.text) !== null);
const addedRules = added.filter((l) => isConstraints(l.file) && ruleKey(l.text) !== null);
for (const r of removedRules) {
  const a = addedRules.find((x) => ruleKey(x.text) === ruleKey(r.text));
  if (!a) {
    if (!isException(r.text)) flag('rule-removed', r.file, r.text);
    continue;
  }
  const before = thresholds(r.text), after = thresholds(a.text);
  let verdict = null;
  for (const dir of ['min', 'max', null]) {
    const was = before.filter((x) => x.dir === dir), now = after.filter((x) => x.dir === dir);
    was.forEach((b, i) => {
      const n = now[i];
      if (verdict) return;
      if (!n) verdict = 'threshold-removed';
      else if (n.n === b.n) return;
      else if (dir === 'min' ? n.n < b.n : dir === 'max' ? n.n > b.n : true) {
        verdict = dir ? 'threshold-loosened' : 'threshold-changed';
      }
    });
  }
  if (verdict) flag(verdict, r.file, r.text + '  ->  ' + a.text);
}

// 3. A stale exception: expired (or undated) rows lift nothing and surface here.
for (const r of exceptionRows) if (r.expired) flag('exception-expired', 'CONSTRAINTS.md', r.id + ' expires ' + (r.expires || '(none)') + ': ' + r.line);

for (const c of changed) {
  if (only.size !== 0 && !only.has(c.file)) continue;
  console.log(`floor-guard: assertion-changed ${c.file}${c.block ? ' [' + c.block + ']' : ''}: ${normalise(c.from).slice(0, 90)}  ->  ${normalise(c.to).slice(0, 90)}`);
}
for (const l of lifted.values()) {
  console.log(`floor-guard: lifted by exception ${l.id} [${l.rule}] ${l.file}${l.count > 1 ? ' (x' + l.count + ')' : ''}`);
}
if (findings.length === 0) { console.log('floor-guard: clean'); process.exit(0); }
console.error('floor-guard: ' + findings.length + ' floor violation(s):');
for (const f of findings) console.error(`  [${f.rule}] ${f.file}: ${f.text}`);
if (findings.some((f) => f.rule === 'rule-removed')) {
  console.error('\nA rule-removed finding can also mean the rule\'s label changed: rename a rule in one commit and change its thresholds in another.');
}
if (findings.some((f) => f.rule === 'threshold-removed')) {
  console.error('\nA threshold-removed finding can also mean a number gained or lost its direction words: compare the two lines before assuming a threshold was deleted.');
}
console.error('\nEach is a move that lowers the bar. Fix the code, or route it through a tracked exception in CONSTRAINTS.md.');
process.exit(1);
