#!/usr/bin/env bash
# Token check for the web app (spec 006 DD3): keeps colour, spacing and component styling in tokens.css
# and the ui primitives. Run by check.sh fast/task/hook on the changed files under src/web/src; never on
# the whole tree in phase 1.
#
#   src/scripts/check_web_tokens.sh [file...]   check the files (paths outside the root are ignored)
#   src/scripts/check_web_tokens.sh --self-test run every rule against fixtures in a temporary root
#
# The root is src/web/src, or $CRIBA_TOKENS_ROOT. Rules, one function each (add new ones to RULES):
#   (a) colour literal    a hex colour or a colour function call (rgb(, hsl(, oklch(, ...) in CSS, TS or TSX.
#                         Allowed only in styles/tokens.css and charts/theme.ts. Not matched: #root, href="#add",
#                         url(#id), an id selector such as `#add {`.
#   (b) inline style      `style={{` in TSX without a `/* geometry */` marker on the same line.
#   (c) legacy markup     className="btn / className="data / a raw <table in TSX, outside components/ui/.
#   (d) raw px            a px value in padding, margin, gap, inset, top, right, bottom or left, outside
#                         styles/tokens.css.
#   (e) avoided term      a term from an `_Avoid_:` line of CONTEXT.md, in a .ts or .tsx file (never *.d.ts)
#                         on a line that is not comment-only (spec-frontend-redesign.shell-docs SD1-SD3). The
#                         terms are read each run from $CRIBA_CONTEXT_FILE, default CONTEXT.md at the repo root.
# styles/app.css is exempt from (c) and (d) until phase 5. Findings print as `path:line: (rule) message`, and
# (e) as `path:line: avoided term "<term>" (CONTEXT.md: use <Term>)`; the exit status is 1 when there are any.
# Exit status 2 is a bad flag. Exit status 3 means the term file failed closed: it is missing or unreadable,
# holds no avoided term, or holds fewer avoided terms than `_Avoid_:` lines; nothing is checked then.
set -euo pipefail
export LC_ALL=C

self="${BASH_SOURCE[0]}"
repo="$(cd "$(dirname "$self")/../.." && pwd -P)"
root="${CRIBA_TOKENS_ROOT:-$repo/src/web/src}"
context_file="${CRIBA_CONTEXT_FILE:-$repo/CONTEXT.md}"

RULES=(rule_colour rule_inline_style rule_legacy_markup rule_raw_px rule_avoided_terms)

# Rule (e) data. A term is also skipped when it is itself a defined `**Term**:` entry of CONTEXT.md (computed:
# Abandoned, Knockout, rejection, Interview, Review). Every other skip is listed here, one entry per line with its
# reason, and only for a term with a real hit in src/web/src (spec SD2). No file or line is ever allow-listed.
EXEMPT=()
EXEMPT+=(status)     # the API parameter and field (`status=open`, a slot's status) and ordinary English; the avoid concerns an Outcome
EXEMPT+=(Filter)     # the `.filter()` method, `filters` parameters and the "Filter Applications" form labels; the avoid concerns a Knock-out Criterion
EXEMPT+=(address)    # an Interview's or a slot's `address` field; the avoid concerns a Zone
EXEMPT+=(Call)       # model calls: the Economics "Calls" column and the `failed_call` label; not an Interview
EXEMPT+=(Flag)       # the generated `Flag` type and the `flag[]` filter; the avoid concerns an Escalation
EXEMPT+=(alert)      # ARIA `role="alert"` on every error notice; the avoid concerns an Escalation
EXEMPT+=(cancelled)  # the `cancelled` slot status, not a Withdrawn Application
EXEMPT+=(paused)     # the agent during a Takeover, and the stage's playback state; not a Stalled Application
EXEMPT+=(expired)    # the Jev reading status from getJevReadings (ADR 0066), not an Abandoned Application (CONTEXT.md:125); owner decision 2026-10-12, jev-readings-web spec JR4
EXEMPT+=(malice)     # the JevReading.malice contract field (ADR 0071), read by jev-intent-web; the visible label is "Manipulation", never the word; owner decision 2026-10-12
# Phrases blanked before matching, case-insensitively, so the avoided word inside them is not a finding.
PHRASES=()
PHRASES+=("driver's license")  # the Knock-out Criterion's own name; the bare word "driver" for a Candidate stays banned
PHRASES+=("Opt-out lifted")    # the event label about an Opted out Candidate; the avoid concerns an Erasure request
PHRASES+=("Not an assessment of the Candidate")  # the Operator panel's fixed line (owner-accepted, ui-detail spec concern 10); the avoid concerns Screening, as CONTEXT.md:147 uses it
PHRASES+=("Jev did not read this message (Jev off, a Takeover, an opt-out or a failure).")  # the Jev readings empty-state line (owner decision 2026-10-12, jev-readings-web spec JR4); the avoid concerns an Erasure request, CONTEXT.md:31 lists Opt-out as avoided
# Terms matched only in their capitalised form, and terms matched without a plural `s` or `es`.
CAPITALISED=()
CAPITALISED+=(Result)  # `result` is everywhere in code; the copy word is the capitalised label
CAPITALISED+=(Case)    # `case` is the JavaScript keyword
SINGULAR=()
SINGULAR+=(Result)     # "Results" is the live name of the Results dashboard

# strip_comments KIND FILE: the file with comments blanked, one output line per input line so line numbers
# hold. KIND is css (block comments) or ts (block comments and // comments).
strip_comments() {
  awk -v kind="$1" '
    BEGIN { inc = 0 }
    {
      s = $0; out = ""
      while (length(s) > 0) {
        if (inc) {
          i = index(s, "*/")
          if (i == 0) { s = "" } else { inc = 0; s = substr(s, i + 2) }
        } else {
          i = index(s, "/*")
          if (i == 0) { out = out s; s = "" } else { out = out substr(s, 1, i - 1); inc = 1; s = substr(s, i + 2) }
        }
      }
      if (kind == "ts") sub(/(^|[ \t])\/\/.*/, "", out)
      print out
    }' "$2"
}

# colour_hex KIND: reads stripped lines on stdin, prints `line:text` for each line holding a hex colour.
# A hex colour is # plus 3, 4, 6 or 8 hex digits that ends the word.
# css: a hex counts only inside a declaration value. The scan keeps that state across lines, so a value that
#   continues on the next line is covered: a `:` opens a declaration unless the statement ends in `{` (a
#   selector such as `a:hover #bad {`); `;`, `{` and `}` close it. A `:` with no terminator on its line opens a
#   declaration only when a space or the end of the line follows it (`color: red,` yes, `a:hover,` no).
#   url(#id) is dropped first, so ids in selectors and urls never match.
# ts: a hex counts only inside a quoted string, and not when the string is an anchor or selector
#   (href="#add", to="#add", id=, htmlFor=, querySelector("#add")).
colour_hex() {
  awk -v kind="$1" -v sq="'" '
    function is_word(c) { return c ~ /[A-Za-z0-9_-]/ }
    function odd(s, ch,   t) { t = s; return (gsub(ch, "", t) % 2) == 1 }
    function colour_len(len) { return len == 3 || len == 4 || len == 6 || len == 8 }
    BEGIN { state = 0 }
    {
      line = $0
      gsub(/url\([^)]*\)/, "", line)
      hit = 0
      if (kind == "css") {
        n = length(line)
        for (i = 1; i <= n; i++) {
          c = substr(line, i, 1)
          if (c == "{" || c == ";" || c == "}") state = 0
          else if (c == ":" && state == 0) {
            tail = substr(line, i + 1)
            if (match(tail, /[{;}]/)) state = (substr(tail, RSTART, 1) == "{") ? 0 : 2
            else state = (tail == "" || substr(tail, 1, 1) == " ") ? 2 : 0
          } else if (c == "#" && state == 2) {
            j = i + 1
            while (j <= n && substr(line, j, 1) ~ /[0-9a-fA-F]/) j++
            if (colour_len(j - i - 1) && !is_word(substr(line, j, 1))) hit = 1
            i = j - 1
          }
        }
      } else {
        rest = line
        before = ""
        while (match(rest, /#[0-9a-fA-F]+/)) {
          len = RLENGTH - 1
          tok_start = RSTART
          nxt = substr(rest, RSTART + RLENGTH, 1)
          pre = before substr(rest, 1, RSTART - 1)
          tail = substr(rest, RSTART + RLENGTH)
          if (colour_len(len) && !is_word(nxt) && (odd(pre, "\"") || odd(pre, sq) || odd(pre, "`"))) {
            q = substr(pre, length(pre), 1)
            exempt = 0
            if (q == "\"" || q == sq || q == "`") {
              head = substr(pre, 1, length(pre) - 1)
              if (head ~ /(href|xlinkHref|to|id|htmlFor|querySelector|querySelectorAll|getElementById|aria-[a-z]+)[=(]\{?$/) exempt = 1
            }
            if (!exempt) hit = 1
          }
          before = pre substr(rest, tok_start, len + 1)
          rest = tail
        }
      }
      if (hit) print NR ":" $0
    }'
}

# emit RULE MESSAGE REL: turns `line:text` records on stdin into `REL:line: (RULE) MESSAGE`.
emit() {
  REL="$3" awk -F: -v r="$1" -v m="$2" '{ print ENVIRON["REL"] ":" $1 ": (" r ") " m }'
}

# load_terms FILE: the avoided terms of a CONTEXT.md-shaped FILE, one `key<TAB>mode<TAB>plural<TAB>term<TAB>use`
# record each. Every `_Avoid_:` line is split on commas, with parentheticals dropped, and each term remembers the
# `**Term**:` entry it came from (a term avoided under two entries names both). Defined terms and EXEMPT are
# left out; mode is c for CAPITALISED (matched as written) and i otherwise (key lower-cased); plural is 0 for
# SINGULAR. Fails closed with status 3 and a message: a missing or unreadable file, no avoided term, fewer
# avoided terms than `_Avoid_:` lines, or an `_Avoid_:` line with no entry above it.
load_terms() {
  local file="$1"
  if [[ ! -f "$file" || ! -r "$file" ]]; then
    echo "check_web_tokens.sh: cannot read the term file $file" >&2
    return 3
  fi
  # ${A[@]+"${A[@]}"}: an emptied list must not trip set -u on bash 3.2
  EXEMPT_LIST="$(printf '%s\n' ${EXEMPT[@]+"${EXEMPT[@]}"})" \
    CAPITALISED_LIST="$(printf '%s\n' ${CAPITALISED[@]+"${CAPITALISED[@]}"})" \
    SINGULAR_LIST="$(printf '%s\n' ${SINGULAR[@]+"${SINGULAR[@]}"})" awk -v file="$file" '
    function set_of(name, arr,   n, i, parts) {
      n = split(ENVIRON[name], parts, "\n")
      for (i = 1; i <= n; i++) if (parts[i] != "") arr[tolower(parts[i])] = 1
    }
    function trim(s) { sub(/^[ \t]+/, "", s); sub(/[ \t\r]+$/, "", s); return s }
    function fail(msg) {
      printf "check_web_tokens.sh: the term file %s %s\n", file, msg > "/dev/stderr"
      failed = 1; exit 3
    }
    BEGIN { set_of("EXEMPT_LIST", exempt); set_of("CAPITALISED_LIST", capital); set_of("SINGULAR_LIST", singular) }
    /^#/ { owner = ""; next }
    /^\*\*.+\*\*:/ { t = $0; sub(/^\*\*/, "", t); sub(/\*\*:.*$/, "", t); owner = t; defined[tolower(t)] = 1; next }
    /^_Avoid_:/ {
      lines++
      if (owner == "") fail("has an _Avoid_: line with no **Term**: entry above it (line " NR ")")
      s = $0; sub(/^_Avoid_:/, "", s); gsub(/\([^)]*\)/, "", s)
      k = split(s, parts, ",")
      for (i = 1; i <= k; i++) { w = trim(parts[i]); if (w != "") { n++; word[n] = w; from[n] = owner } }
    }
    END {
      if (failed) exit 3
      if (n == 0) fail("holds no avoided term")
      if (n < lines) fail("holds " n " avoided terms on " lines " _Avoid_: lines")
      for (i = 1; i <= n; i++) {
        key = tolower(word[i])
        if (key in defined || key in exempt) continue
        if (key in use) {
          if (index(" or " use[key] " or ", " or " from[i] " or ") == 0) use[key] = use[key] " or " from[i]
          continue
        }
        use[key] = from[i]; shown[key] = word[i]; order[++m] = key
      }
      for (j = 1; j <= m; j++) {
        key = order[j]
        if (key in capital) printf "%s\tc", toupper(substr(key, 1, 1)) substr(key, 2)
        else printf "%s\ti", key
        printf "\t%d\t%s\t%s\n", (key in singular) ? 0 : 1, shown[key], use[key]
      }
    }' "$file"
}

# --- rules: each takes (file, rel) and prints findings, nothing when clean ---------------------------------

rule_colour() {
  local file="$1" rel="$2" kind=ts
  case "$rel" in styles/tokens.css|charts/theme.ts) return 0 ;; esac
  [[ "$rel" == *.css ]] && kind=css
  local stripped
  stripped="$(strip_comments "$kind" "$file")"
  {
    colour_hex "$kind" <<<"$stripped"
    # A colour function: rgb(, rgba(, hsl(, hsla(, oklch(, oklab(, lch(, lab(, hwb(, color(.
    grep -nE '(^|[^A-Za-z0-9_.-])(rgba?|hsla?|oklch|oklab|lch|lab|hwb|color)\(' <<<"$stripped" || true
  } | sort -t: -k1,1n -u | emit a "colour literal: use a token from styles/tokens.css" "$rel"
}

rule_inline_style() {
  local file="$1" rel="$2"
  [[ "$rel" == *.tsx ]] || return 0
  grep -nE 'style=\{\{' "$file" | grep -v '/\* geometry \*/' |
    emit b "inline style: move it to a stylesheet, or mark real geometry with /* geometry */" "$rel" || true
}

rule_legacy_markup() {
  local file="$1" rel="$2"
  [[ "$rel" == *.tsx ]] || return 0
  case "$rel" in components/ui/*) return 0 ;; esac
  strip_comments ts "$file" | grep -nE 'className="(btn|data)|<table([^A-Za-z0-9_-]|$)' |
    emit c "legacy markup: use the components/ui primitive" "$rel" || true
}

rule_raw_px() {
  local file="$1" rel="$2" kind=ts
  case "$rel" in styles/tokens.css|styles/app.css) return 0 ;; esac
  [[ "$rel" == *.css || "$rel" == *.ts || "$rel" == *.tsx ]] || return 0
  # A TS object value ends at a comma; a CSS value may hold one (var(--a, 4px)).
  local value='[^;{}]*'
  if [[ "$rel" == *.css ]]; then kind=css; else value='[^;{},]*'; fi
  strip_comments "$kind" "$file" |
    grep -nE "(^|[^A-Za-z0-9_-])(padding|margin|gap|row-gap|column-gap|grid-gap|inset|top|right|bottom|left)[A-Za-z-]*[\"']?[[:space:]]*:${value}[0-9.]+px" |
    emit d "raw px: use a --space-* token" "$rel" || true
}

# rule_avoided_terms: the terms load_terms read, in .ts and .tsx files only (never *.d.ts). Comment-only lines
# (first non-space characters //, /* or *) are skipped; a trailing comment on a code line is scanned. PHRASES
# are blanked first. A term is matched literally (index(), no regex, so `-`, `/` and `.` in a term are plain
# characters), with a boundary on each side. A boundary is the line's edge, an ASCII character that is not a
# letter, a digit, `_` or `-`, or a UTF-8 space or punctuation character: U+00A0-U+00BF except the letters
# ª µ º (bytes C2 A0-BF: no-break space, ¡ « » ¿) and U+2000-U+203F (bytes E2 80 xx: spaces, dashes, quotes,
# ellipsis). Any other byte above ASCII counts as a letter, such as the ó in botón. The awk works on bytes
# (LC_ALL=C), so the bytes before a term are read back as one character. A plural `s` or `es` may follow
# unless the term is SINGULAR. Mode i compares lower-cased text, mode c the text as written.
rule_avoided_terms() {
  local file="$1" rel="$2"
  case "$rel" in *.d.ts) return 0 ;; *.ts|*.tsx) ;; *) return 0 ;; esac
  PHRASE_LIST="$(printf '%s\n' ${PHRASES[@]+"${PHRASES[@]}"})" TERM_TABLE="$avoid_table" REL="$rel" awk '
    function ends_before(hay, s,   c) {  # a boundary ends at byte s - 1
      if (s == 1) return 1
      c = substr(hay, s - 1, 1)
      if (index(bound, c)) return 1
      if (s > 2 && substr(hay, s - 2, 1) == "\302" && index(latin1, c)) return 1
      return s > 3 && substr(hay, s - 3, 2) == "\342\200"
    }
    function starts_at(hay, e,   c, d) {  # a boundary starts at byte e
      c = substr(hay, e, 1)
      if (c == "" || index(bound, c)) return 1
      d = substr(hay, e + 1, 1)
      if (c == "\302" && d != "" && index(latin1, d)) return 1
      return substr(hay, e, 2) == "\342\200"
    }
    function blank(n,   s) { s = ""; while (length(s) < n) s = s " "; return s }
    function found(hay, k, plural,   pos, i, s, e) {
      pos = 1
      while ((i = index(substr(hay, pos), k)) > 0) {
        s = pos + i - 1; e = s + length(k)
        if (ends_before(hay, s)) {
          if (starts_at(hay, e)) return 1
          if (plural && substr(hay, e, 1) == "s" && starts_at(hay, e + 1)) return 1
          if (plural && substr(hay, e, 2) == "es" && starts_at(hay, e + 2)) return 1
        }
        pos = s + 1
      }
      return 0
    }
    BEGIN {
      for (i = 1; i < 127; i++) { c = sprintf("%c", i); if (c !~ /[A-Za-z0-9_-]/) bound = bound c }
      # second bytes of C2 A0-BF, without AA, B5 and BA (ª µ º)
      latin1 = "\240\241\242\243\244\245\246\247\250\251\253\254\255\256\257\260\261\262\263\264\266\267\270\271\273\274\275\276\277"
      nt = split(ENVIRON["TERM_TABLE"], rows, "\n")
      for (i = 1; i <= nt; i++) {
        split(rows[i], f, "\t"); key[i] = f[1]; mode[i] = f[2]; plural[i] = f[3] + 0; shown[i] = f[4]; use[i] = f[5]
      }
      np = split(ENVIRON["PHRASE_LIST"], phrase, "\n")
      for (i = 1; i <= np; i++) phrase[i] = tolower(phrase[i])
    }
    /^[ \t]*(\/\/|\/\*|\*)/ { next }
    {
      line = $0; low = tolower(line)
      for (p = 1; p <= np; p++) {
        if (phrase[p] == "") continue
        while ((k = index(low, phrase[p])) > 0) {
          pad = blank(length(phrase[p]))
          low = substr(low, 1, k - 1) pad substr(low, k + length(phrase[p]))
          line = substr(line, 1, k - 1) pad substr(line, k + length(phrase[p]))
        }
      }
      for (t = 1; t <= nt; t++) {
        if (key[t] == "") continue
        if (found(mode[t] == "c" ? line : low, key[t], plural[t]))
          print ENVIRON["REL"] ":" NR ": avoided term \"" shown[t] "\" (CONTEXT.md: use " use[t] ")"
      }
    }' "$file"
}

# --- driver ----------------------------------------------------------------------------------------------

# check_files FILE...: run every rule on each file under the root; print findings; status 1 if any.
check_files() {
  local root_abs f abs dir rel rule out found=0
  avoid_table="$(load_terms "$context_file")" || exit 3  # global: rule_avoided_terms reads it
  root_abs="$(cd "$root" && pwd -P)"
  for f in "$@"; do
    [[ "$f" == /* ]] || f="$PWD/$f"
    [[ -f "$f" ]] || continue
    dir="$(cd "$(dirname "$f")" && pwd -P)"
    abs="$dir/$(basename "$f")"
    rel="${abs#"$root_abs"/}"
    [[ "$rel" != "$abs" ]] || continue  # outside the root
    case "$rel" in *.css|*.ts|*.tsx) ;; *) continue ;; esac
    for rule in "${RULES[@]}"; do
      out="$("$rule" "$abs" "$rel")"
      if [[ -n "$out" ]]; then
        echo "$out"
        found=1
      fi
    done
  done
  return "$found"
}

# --- self-test -------------------------------------------------------------------------------------------

self_test() {
  local failures=0 cases=0
  tmp="$(mktemp -d)"  # global: the EXIT trap runs after this function returns
  trap 'rm -rf "$tmp"' EXIT
  mkdir -p "$tmp/styles" "$tmp/charts" "$tmp/components/ui" "$tmp/pages"
  # The avoided terms always come from the real CONTEXT.md; the files never come from the real tree.
  export CRIBA_CONTEXT_FILE="$repo/CONTEXT.md"

  # run REL CONTENT: write the fixture and run the check on it; sets status and output.
  run() {
    printf '%s\n' "$2" > "$tmp/$1"
    status=0
    output="$(CRIBA_TOKENS_ROOT="$tmp" bash "$self" "$tmp/$1" 2>&1)" || status=$?
  }
  flagged() {  # flagged RULE REL CONTENT
    cases=$((cases + 1)); run "$2" "$3"
    if [[ $status -ne 1 || "$output" != *"($1)"* ]]; then
      failures=$((failures + 1)); echo "FAIL expected ($1) finding in $2: $3 (status $status, output: $output)"
    fi
  }
  clean() {  # clean REL CONTENT
    cases=$((cases + 1)); run "$1" "$2"
    if [[ $status -ne 0 || -n "$output" ]]; then
      failures=$((failures + 1)); echo "FAIL expected no finding in $1: $2 (status $status, output: $output)"
    fi
  }
  avoided() {  # avoided REL TERM USE CONTENT: the exact avoided-term finding on line 1
    cases=$((cases + 1)); run "$1" "$4"
    if [[ $status -ne 1 || "$output" != *"$1:1: avoided term \"$2\" (CONTEXT.md: use $3)"* ]]; then
      failures=$((failures + 1)); echo "FAIL expected avoided term \"$2\" (use $3) in $1: $4 (status $status, output: $output)"
    fi
  }
  fails_closed() {  # fails_closed NAME TERM_FILE: a term file that cannot give the terms stops the check
    cases=$((cases + 1))
    printf 'const a = <p>Customers</p>;\n' > "$tmp/pages/closed.tsx"
    status=0
    output="$(CRIBA_CONTEXT_FILE="$2" CRIBA_TOKENS_ROOT="$tmp" bash "$self" "$tmp/pages/closed.tsx" 2>&1)" || status=$?
    if [[ $status -ne 3 || "$output" != *"term file"* ]]; then
      failures=$((failures + 1)); echo "FAIL expected exit 3 and a term file message for $1 (status $status, output: $output)"
    fi
  }

  # (a) colour literal
  flagged a pages/a1.css 'a { color: #fff; }'
  flagged a pages/a2.css 'a { color: #1a2b3c; }'
  flagged a pages/a3.css 'a { background: rgba(0, 0, 0, 0.5); }'
  flagged a pages/a4.css 'a { border: 1px solid #ccc8; }'
  flagged a pages/a5.tsx 'const c = "#1a2b3c";'
  flagged a pages/a6.tsx 'const c = "1px solid #ccc";'
  flagged a pages/a7.ts 'const c = hsl(10, 20%, 30%);'
  flagged a styles/a8.css 'a { color: oklch(0.5 0.1 120); }'
  flagged a pages/a21.css $'a {\n  background:\n    #fff;\n}'
  flagged a pages/a22.css $'a {\n  box-shadow: 0 0 1px red,\n    0 0 2px #123456;\n}'
  flagged a pages/a23.css $'a {\n  background: linear-gradient(\n    #fff,\n    #000\n  );\n}'
  clean pages/a24.css $'#add,\n#bad {\n  display: block;\n}'
  clean pages/a25.css $'a:hover,\nb:focus #abc {\n  display: none;\n}'
  clean pages/a26.css $'a {\n  color: var(--ink);\n}\n#abc { display: block; }'
  clean pages/a27.tsx 'const t = <p>Order #1234</p>;'
  clean pages/a9.css '#root { margin: 0; }'
  clean pages/a10.css '#add { display: block; }'
  clean pages/a11.css 'a:hover #bad { display: none; }'
  clean pages/a12.css 'rect { fill: url(#abc); }'
  clean pages/a13.css '/* the seed was #f6f3ec */ a { color: var(--ink); }'
  clean pages/a14.tsx 'const a = <a href="#add">Add</a>;'
  clean pages/a15.tsx 'const r = <Link to="#bad" />;'
  clean pages/a16.tsx 'const r = document.querySelector("#add");'
  clean pages/a17.tsx 'const r = <svg><use href="#abc" /></svg>; // fill #fff'
  clean pages/a18.tsx 'const root = document.getElementById("root");'
  clean styles/tokens.css ':root { --a: light-dark(#fff, rgba(0, 0, 0, 0.5)); }'
  clean charts/theme.ts 'export const grid = "#e1e0d9";'

  # (b) inline style
  flagged b pages/b1.tsx 'const a = <div style={{ width: 3 }} />;'
  clean pages/b2.tsx 'const a = <div style={{ width: 3 }} /* geometry */ />;'
  clean pages/b3.tsx 'const a = <div className="x" />;'

  # (c) legacy markup (TSX only; the app.css legacy selector is exempt)
  flagged c pages/c1.tsx 'const a = <button className="btn primary">Go</button>;'
  flagged c pages/c2.tsx 'const a = <table className="data" />;'
  flagged c pages/c3.tsx 'const a = <table>{null}</table>;'
  flagged c pages/c4.tsx 'const a = <div className="data-x" />;'
  clean components/ui/c5.tsx 'const a = <table className="data" />;'
  clean pages/c6.tsx 'const a = <Table />;'
  clean styles/app.css '.btn { font: inherit; } table.data { width: 100%; }'

  # (d) raw px
  flagged d pages/d1.css 'a { padding: 8px 16px; }'
  flagged d pages/d2.css 'a { margin-top: 4px; }'
  flagged d pages/d3.css 'a { gap: 12px; }'
  flagged d pages/d4.css 'a { inset: 0 4px; }'
  flagged d pages/d5.css 'a { top: 10px; }'
  flagged d pages/d6.tsx 'const s = { paddingLeft: "8px" };'
  flagged d pages/d10.css 'a { row-gap: 8px; }'
  clean pages/d11.tsx 'const s = { margin: 0, width: "4px" };'
  clean pages/d7.css 'a { padding: var(--space-2); }'
  clean pages/d8.css 'a { border-right: 1px solid var(--seam); width: 4px; }'
  clean pages/d9.css '.top-bar:hover { color: var(--ink); }'
  clean styles/app.css '.btn { padding: 8px 16px; margin: 4px; gap: 6px; }'
  clean styles/tokens.css ':root { --space-1: 4px; }'

  # (e) avoided terms, read from CONTEXT.md
  avoided pages/t1.tsx Customer Client 'const a = <p>Customers waiting</p>;'
  avoided pages/t2.ts 'Hiring staff' Recruiter 'export const who = "Hiring staff";'
  avoided pages/t3.tsx Result Outcome 'const a = <dt>Result</dt>;'
  avoided pages/t4.tsx 'Control panel' Overview 'const a = <h1>Control panel</h1>;'
  avoided pages/t5.tsx Inbox Today 'const a = <nav>Inbox</nav>;'
  avoided pages/t6.ts 'Control panel' Overview 'export const title = "control panels";'
  avoided pages/t7.tsx 'Demo mode' 'Demo stage' 'const a = <h1>Demo mode</h1>;'
  avoided pages/t8.tsx 'Debug panel' 'Presenter console' 'const a = <aside aria-label="Debug panel" />;'
  avoided pages/t9.ts 'Demo run' Take 'export const label = "New demo run";'
  avoided pages/t10.ts Customer Client 'const n = 1; // Customer'
  avoided pages/t11.tsx Case Application 'const a = <th>Case</th>;'
  avoided pages/t12.tsx driver Candidate 'const a = <p>New driver</p>;'
  avoided pages/t13.ts Opt-out 'Erasure request' 'export const label = "Opt-out request";'
  avoided pages/t14.ts Disqualification 'Knockout or Rejection' 'export const label = "Disqualification";'
  # UTF-8 punctuation and spaces are boundaries: ’ … “ ” — (E2 80 xx), no-break space « » (C2 xx)
  avoided pages/t15.tsx Customer Client $'const a = <p>Customer\xe2\x80\x99s view</p>;'
  avoided pages/t16.tsx Customer Client $'const a = <p>Loading customers\xe2\x80\xa6</p>;'
  avoided pages/t17.tsx Customer Client $'const a = <p>\xe2\x80\x9cCustomer\xe2\x80\x9d</p>;'
  avoided pages/t18.tsx lead Candidate $'const a = <p>Lead\xe2\x80\x94new</p>;'
  avoided pages/t19.tsx Customer Client $'const a = <p>Customer\xc2\xa0list</p>;'
  avoided pages/t20.tsx Customer Client $'const a = <p>New\xc2\xa0customer</p>;'
  avoided pages/t21.tsx Customer Client $'const a = <p>\xc2\xabCustomers\xc2\xbb</p>;'
  clean pages/u1.ts 'const handBack = (id: string) => id;'
  clean pages/u2.ts 'const field = "driver_license";'
  clean pages/u3.tsx 'const a = <span className="status-badge" />;'
  clean pages/u4.tsx 'const a = <h1>Results</h1>;'
  clean pages/u5.tsx $'// Customers and leads\n/* Hiring staff */\n * Result\n  // Inbox'
  clean pages/u6.ts 'export const label = "Driver'"'"'s license";'
  clean pages/u7.tsx 'const a = <p>Knockout</p>;'
  clean pages/u8.tsx 'const a = <p>Open status</p>;'
  clean pages/u9.d.ts 'export type Customer = { lead: string };'
  clean pages/u10.tsx 'const a = <div className="customer-row" />;'
  clean pages/u11.ts 'const id = row.handoff_id;'
  clean pages/u12.ts 'switch (x) { case 1: return result; }'
  clean pages/u13.ts 'export const label = "Opt-out lifted";'
  clean pages/u17.ts 'export const label = "Jev did not read this message (Jev off, a Takeover, an opt-out or a failure).";'
  avoided pages/t22.ts Opt-out 'Erasure request' 'export const label = "Jev did not read this message (an opt-out request).";'
  clean pages/u18.ts 'export const label = "expired";'
  clean pages/u19.ts 'const score = reading.malice;'
  clean pages/u14.tsx 'const a = <p>Pulsa el botón</p>;'
  clean pages/u16.tsx $'const a = <p>\xc3\xa9bot</p>;'  # é: its second byte A9 is not a C2 A9
  clean pages/u15.css '.lead { display: block; }'
  # every EXEMPT, PHRASES, CAPITALISED and SINGULAR entry has its reason on its own line, and EXEMPT has entries
  cases=$((cases + 1))
  exempt_entries="$(grep -c -E '^EXEMPT\+=\(' "$self" || true)"
  bare="$(grep -E '^(EXEMPT|PHRASES|CAPITALISED|SINGULAR)\+=\(' "$self" | grep -v -E '\)[[:space:]]+# [^[:space:]]' || true)"
  if [[ "$exempt_entries" -eq 0 || -n "$bare" ]]; then
    failures=$((failures + 1)); echo "FAIL every exemption needs a reason on its line ($exempt_entries EXEMPT entries; without a reason: $bare)"
  fi

  # the root and the file type
  cases=$((cases + 1))
  outside="$(mktemp -d)"
  printf 'a { color: #fff; padding: 8px; }\n' > "$outside/out.css"
  status=0
  output="$(CRIBA_TOKENS_ROOT="$tmp" bash "$self" "$outside/out.css" 2>&1)" || status=$?
  if [[ $status -ne 0 || -n "$output" ]]; then
    failures=$((failures + 1)); echo "FAIL a file outside the root must be ignored (status $status, output: $output)"
  fi
  rm -rf "$outside"
  clean pages/e1.md '# heading with #fff and padding: 8px'
  clean pages/e2.json '{"color": "#fff"}'

  # the term file fails closed
  : > "$tmp/empty.md"
  fails_closed "an empty term file" "$tmp/empty.md"
  fails_closed "a missing term file" "$tmp/missing.md"
  printf '**One**:\nA term.\n_Avoid_: other\n\n**Two**:\nA term.\n_Avoid_: (a note only)\n' > "$tmp/short.md"
  fails_closed "fewer terms than _Avoid_: lines" "$tmp/short.md"

  if (( failures )); then
    echo "check_web_tokens.sh --self-test: $failures of $cases cases failed" >&2
    return 1
  fi
  echo "check_web_tokens.sh --self-test: $cases cases passed"
}

case "${1:-}" in
  --self-test) self_test ;;
  -*) echo "usage: check_web_tokens.sh [--self-test | file...]" >&2; exit 2 ;;
  *) check_files "$@" ;;
esac
