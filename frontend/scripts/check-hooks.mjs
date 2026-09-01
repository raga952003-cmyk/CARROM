/**
 * Flag React hooks that sit after an early return.
 *
 * A component must call the same hooks in the same order on every render. Put a
 * `useState` below an `if (!data) return ...` and the first render (no data)
 * runs fewer hooks than the second (data arrived); React then tears the whole
 * screen down with "Rendered more hooks than during the previous render".
 *
 * TypeScript cannot see this — `npx tsc --noEmit` was clean while BoardMode,
 * the umpire's phone screen, crashed on open and again whenever a board ran
 * out of matches. eslint-plugin-react-hooks is the real tool; this is the
 * version that needs no dependencies and catches the same class, so there is
 * something standing between the bug and a phone at a carrom board.
 *
 *     node scripts/check-hooks.mjs
 *
 * Exit code is the number of offences, so it can gate a build.
 *
 * The distinction that matters is FUNCTION nesting, not brace nesting. A return
 * inside `if (!x) { ... }` is an early exit from the component even though it
 * is two braces deep; a return inside `useState(() => { ... })` or `.map(x =>
 * { ... })` is not an exit from anything and says nothing about hook order.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';

// The generic parameter list matters: `useState<Board>(x)` puts a `<`
// between the name and the call, and a regex expecting `(` next silently
// matched nothing -- so the checker read clean while the bug was present.
const HOOK = /(?:^|[^.\w])(use[A-Z]\w*)\s*(?:<[^<>()]*>)?\s*\(/;
const STATEFUL = /^use(State|Effect|Memo|Reducer|LayoutEffect|Context)$/;
const COMPONENT = /^(?:export\s+)?(?:const|function)\s+[A-Z]\w*/;
const OPENS_FUNCTION = /(=>\s*\{|\bfunction\b[^;]*\{)/;

function walk(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry);
    if (statSync(p).isDirectory()) out.push(...walk(p));
    else if (entry.endsWith('.tsx')) out.push(p);
  }
  return out;
}

/** Braces on a line, ignoring strings and comments. Returns the sequence. */
function braces(line) {
  const seq = [];
  let inS = null, inBlock = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i], n = line[i + 1];
    if (inBlock) { if (c === '*' && n === '/') { inBlock = false; i++; } continue; }
    if (inS) { if (c === '\\') { i++; continue; } if (c === inS) inS = null; continue; }
    if (c === '/' && n === '/') break;
    if (c === '/' && n === '*') { inBlock = true; i++; continue; }
    if (c === '"' || c === "'" || c === '`') { inS = c; continue; }
    if (c === '{' || c === '}') seq.push(c);
  }
  return seq;
}

let offences = 0;
for (const file of walk('src')) {
  const lines = readFileSync(file, 'utf8').split('\n');
  // Each open brace pushes whether it began a function body.
  const stack = [];
  let componentDepth = null;   // stack length inside the component body
  let earlyReturn = 0;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const fnHere = OPENS_FUNCTION.test(line);
    const declaresComponent = COMPONENT.test(line) && fnHere;

    // How many functions are open between here and the component body?
    const nestedFns = componentDepth === null
      ? 0
      : stack.slice(componentDepth).filter(Boolean).length;

    if (!declaresComponent && componentDepth !== null
        && stack.length >= componentDepth && nestedFns === 0) {
      if (/^\s*return[\s(;]/.test(line)) {
        earlyReturn = i + 1;
      } else if (earlyReturn) {
        const m = line.match(HOOK);
        if (m && STATEFUL.test(m[1])) {
          console.error(
            `${file}:${i + 1}  ${m[1]} called after an early return on line ${earlyReturn}`
          );
          offences++;
        }
      }
    }

    for (const b of braces(line)) {
      if (b === '{') stack.push(fnHere);
      else {
        stack.pop();
        if (componentDepth !== null && stack.length < componentDepth) {
          componentDepth = null;
          earlyReturn = 0;
        }
      }
    }

    // Set AFTER this line's braces: a declaration like
    // `({ a, b }) => {` opens and closes the destructuring pattern before it
    // opens the body, and reading the depth mid-line put the component body at
    // a level that the same line then popped straight back out of.
    if (declaresComponent) {
      componentDepth = stack.length;
      earlyReturn = 0;
    }
  }
}

console.log(offences ? `\n${offences} hook order problem(s)` : 'No hooks after early returns.');
process.exit(offences);
