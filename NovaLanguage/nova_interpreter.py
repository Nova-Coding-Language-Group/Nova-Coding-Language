#!/usr/bin/env python3
"""
nova_interpreter.py  -  Nova interpreter (terminal only)
=========================================================
Run a .nova file:   python nova_interpreter.py my_script.nova
Interactive REPL:   python nova_interpreter.py
  Type Nova code, blank line to execute, Ctrl+C to exit.

EXPRESSIONS
  int / float / string literals     42  3.14  "hello"
  variables                         x  myVar
  arithmetic                        + - * /   (/ always true-division -> float)
  modulo                            %
  chained ops                       a + b + c + d
  comparisons -> 0/1                == != < > <= >=
  string concat                     "hi " + name
  unary minus                       -x  -3.14
  parentheses                       (a + b) * c
  array index                       arr[n]  (n = any expression)
  built-in calls                    len  int  float  str  abs  max  min  type
                                    read_file  write_file  ask  put
                                    mem_read  mem_write

STATEMENTS
  // comment           line comment
  ; separator          same as newline
  have x = expr        declare + assign  (arrays:  have x = ["a","b"])
  x = expr             assign (auto-declare)
  x[n] = expr          array element assign
  put(expr)            print to output
  ask(prompt)          read a line from stdin -> string
  txtbox("label") {    print the label, then run the block (terminal: ask() reads stdin)
      ask("prompt")        the ask() inside captures user input
  }
  mem_write(name, val) write val into named shared memory block
  mem_read(name)       read string from named shared memory block
  when(expr){...}      if-block
  whenwise(expr){...}  elif-block (alias: ww)
  ww(expr){...}        short alias for whenwise
  otherwise{...}       else-block after when/whenwise  (alias: else)
  else{...}            alias for otherwise
  while(expr){...}     loop while truthy
  repeat N {...}       loop exactly N times
  break                exit innermost loop
  write_file(p,expr)   write string to file
  multi-stmt line      two-or-more spaces between statements
  inline blocks        when(x){put(1)}otherwise{put(0)}  on one line
"""

import re, sys, os, random as _random
_rng = _random.Random()  # single persistent instance — never re-seeded



# --- tokeniser ----------------------------------------------------------------

TOKEN_SPEC = [
    ('FLOAT',     r'\d+\.\d+'),
    ('INT',       r'\d+'),
    ('STRING',    r'"[^"]*"'),
    ('ID',        r'[A-Za-z_]\w*'),
    ('OP',        r'==|!=|<=|>=|[+\-*/%<>]'),
    ('EQ',        r'='),
    ('LBRACE',    r'\{'), ('RBRACE',  r'\}'),
    ('LPAREN',    r'\('), ('RPAREN',  r'\)'),
    ('LBRACK',    r'\['), ('RBRACK',  r'\]'),
    ('COMMA',     r','),
    ('SEMI',      r';'),
    ('SKIP',      r'[ \t\r\n]+'),
    ('COMMENT',   r'//[^\n]*'),
    ('MISMATCH',  r'.'),
]
_TOK_RE = re.compile('|'.join(f'(?P<{k}>{v})' for k, v in TOKEN_SPEC))

def tokenize(code):
    toks = []
    for m in _TOK_RE.finditer(code):
        k, v = m.lastgroup, m.group()
        if k in ('SKIP', 'COMMENT', 'MISMATCH'): continue
        if   k == 'FLOAT':  v = float(v)
        elif k == 'INT':    v = int(v)
        elif k == 'STRING': v = v[1:-1]
        toks.append({'type': k, 'val': v})
    return toks


# --- preprocessor (multi-stmt + inline-block expander) -----------------------

def _strip_comment(s):
    out, in_str = [], False
    i = 0
    while i < len(s):
        c = s[i]
        if   c == '"' and not in_str: in_str = True;  out.append(c)
        elif c == '"' and in_str:     in_str = False; out.append(c)
        elif c == '/' and not in_str and i+1 < len(s) and s[i+1] == '/': break
        else: out.append(c)
        i += 1
    return ''.join(out).rstrip()

def _split_stmts(s):
    st = s.strip()
    if st.startswith('use '): return [st]
    if st.startswith('have '):
        parts = re.split(r'  +(?=have )', st)
        return [p.strip() for p in parts if p.strip()]
    parts, cur = [], ''
    dp = db = 0; in_str = False; i = 0
    while i < len(s):
        c = s[i]
        if   c == '"' and not in_str: in_str = True;  cur += c
        elif c == '"' and in_str:     in_str = False; cur += c
        elif not in_str and c == '(':  dp += 1; cur += c
        elif not in_str and c == ')':  dp -= 1; cur += c
        elif not in_str and c == '[':  db += 1; cur += c
        elif not in_str and c == ']':  db -= 1; cur += c
        elif not in_str and dp == 0 and db == 0 and c == ';':
            if cur.strip(): parts.append(cur.strip())
            cur = ''
        elif (not in_str and dp == 0 and db == 0
              and c == ' ' and i+1 < len(s) and s[i+1] == ' '):
            if cur.strip(): parts.append(cur.strip())
            cur = ''
            while i < len(s) and s[i] == ' ': i += 1
            continue
        else: cur += c
        i += 1
    if cur.strip(): parts.append(cur.strip())
    return parts or [s.strip()]

def _expand_line(s, base_indent):
    if '{' not in s and '}' not in s:
        return [' '*base_indent + p for p in _split_stmts(s) if p.strip()]
    result = []; cur = ''; depth = 0; in_str = False; dp = 0
    for ch in s:
        if   ch == '"' and not in_str: in_str = True;  cur += ch
        elif ch == '"' and in_str:     in_str = False; cur += ch
        elif not in_str and ch == '(':  dp += 1; cur += ch
        elif not in_str and ch == ')':  dp -= 1; cur += ch
        elif not in_str and dp == 0 and ch == '{':
            for sub in _split_stmts(cur):
                if sub.strip(): result.append(' '*(base_indent + depth*4) + sub.strip())
            cur = ''; depth += 1
        elif not in_str and dp == 0 and ch == '}':
            for sub in _split_stmts(cur):
                if sub.strip(): result.append(' '*(base_indent + depth*4) + sub.strip())
            cur = ''; depth = max(0, depth-1)
        else: cur += ch
    for sub in _split_stmts(cur):
        if sub.strip(): result.append(' '*base_indent + sub.strip())
    return result

def preprocess(source):
    lines = []
    for ln in source.splitlines():
        stripped = ln.strip()
        if not stripped or stripped.startswith('//'): continue
        indent = len(ln) - len(ln.lstrip())
        clean  = _strip_comment(stripped)
        if not clean: continue
        for el in _expand_line(clean, indent):
            if el.strip(): lines.append(el)
    return '\n'.join(lines)

def _inject_braces(preprocessed_text):
    lines = [ln for ln in preprocessed_text.splitlines() if ln.strip()]
    if not lines:
        return preprocessed_text
    has_indent_blocks = any(
        len(ln) - len(ln.lstrip()) > 0 and '{' not in ln and '}' not in ln
        for ln in lines
    )
    if not has_indent_blocks:
        return preprocessed_text
    result = []
    indent_stack = [0]
    for ln in lines:
        indent = len(ln) - len(ln.lstrip())
        if indent > indent_stack[-1]:
            if result:
                result[-1] = result[-1].rstrip() + ' {'
            indent_stack.append(indent)
        else:
            while len(indent_stack) > 1 and indent < indent_stack[-1]:
                result.append(indent_stack[-1] * ' ' + '}')
                indent_stack.pop()
        result.append(ln)
    while len(indent_stack) > 1:
        result.append(indent_stack[-1] * ' ' + '}')
        indent_stack.pop()
    return '\n'.join(result)


# --- shared memory helpers ----------------------------------------------------

def _shm_path(name):
    """Return the temp-file path used for mem_read/mem_write IPC.
    Matches the path the compiled Nova exe uses: TEMP/nova_shm_<name>.txt"""
    import tempfile
    return os.path.join(tempfile.gettempdir(), f"nova_shm_{name}.txt")

def _mem_write(name, value):
    """Write a string to the named IPC temp file."""
    try:
        with open(_shm_path(name), 'w', encoding='utf-8') as f:
            f.write(str(value))
    except Exception as e:
        raise RuntimeError(f"mem_write failed: {e}")

def _mem_read(name):
    """Read a string from the named IPC temp file."""
    try:
        with open(_shm_path(name), 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"ERROR:{e}"


# --- interpreter --------------------------------------------------------------

class BreakSignal(Exception): pass

class NovaInterpreter:
    def __init__(self, tokens, output_fn=None, ask_fn=None):
        self.tokens = tokens
        self.pos    = 0
        self.vars   = {}
        self._out   = output_fn or (lambda s: print(s))
        self._ask   = ask_fn    or (lambda p: input(p))

    # -- token helpers ---------------------------------------------------------

    def peek(self, off=0):
        i = self.pos + off
        return self.tokens[i] if i < len(self.tokens) else None

    def eat(self, typ=None, val=None):
        t = self.peek()
        if t is None: raise SyntaxError("Unexpected end of input")
        if typ and t['type'] != typ:
            raise SyntaxError(f"Expected {typ}, got {t['type']} ({t['val']!r})")
        if val is not None and t['val'] != val:
            raise SyntaxError(f"Expected {val!r}, got {t['val']!r}")
        self.pos += 1; return t

    def match(self, typ=None, val=None):
        t = self.peek()
        if not t: return False
        if typ and t['type'] != typ: return False
        if val is not None and t['val'] != val: return False
        return True

    # -- runner ----------------------------------------------------------------

    def run_all(self):
        while self.pos < len(self.tokens):
            self.statement()

    def statement(self):
        t = self.peek()
        if not t or t['val'] == '}': return

        if t['type'] == 'SEMI': self.eat(); return

        # orphaned chain keywords - skip safely
        if t['type'] == 'ID' and t['val'] in ('whenwise', 'ww', 'otherwise', 'else'):
            self.eat()
            if self.match('LPAREN'):
                self.eat(); depth = 1
                while self.pos < len(self.tokens) and depth:
                    k = self.tokens[self.pos]['type']
                    if k == 'LPAREN': depth += 1
                    elif k == 'RPAREN': depth -= 1
                    self.pos += 1
            if self.match('LBRACE'): self._block()
            return

        # colors/colours block — just run the body, color state is visual-only
        if t['type'] == 'ID' and t['val'] in ('colors', 'colours'):
            self.eat(); self.eat('LPAREN')
            preset = None
            if not self.match('RPAREN'):
                preset = self._display(self.expression())
            self.eat('RPAREN')
            body = self._block()
            if preset:
                self._out(f"[theme: {preset}]")
            # parse body for key=value color assignments and store them
            saved_tokens, saved_pos = self.tokens, self.pos
            self.tokens, self.pos = body, 0
            while self.pos < len(self.tokens):
                ct = self.peek()
                if not ct: break
                if ct['type'] == 'ID' and self.pos + 2 < len(self.tokens):
                    key = ct['val']
                    if self.tokens[self.pos+1]['type'] == 'EQ':
                        self.eat(); self.eat('EQ')
                        val = self._display(self.expression())
                        self.vars[f'_theme_{key}'] = val
                    else:
                        self.eat()
                else:
                    self.eat()
            self.tokens, self.pos = saved_tokens, saved_pos
            return

        if t['type'] == 'ID' and t['val'] == 'have':
            self.eat()
            name = self.eat('ID')['val']
            self.eat('EQ')
            self.vars[name] = self._rhs()
            return

        if t['type'] == 'ID' and t['val'] == 'break':
            self.eat(); raise BreakSignal()

        if t['type'] == 'ID' and t['val'] == 'pause':
            self.eat()
            if self.match('LPAREN'):
                self.eat('LPAREN')
                if self.match('RPAREN'):
                    # pause() with no arg — wait for Enter
                    self.eat('RPAREN')
                    input()
                else:
                    import time as _time
                    ms = self.expression()
                    self.eat('RPAREN')
                    _time.sleep(float(ms) / 1000.0)
            else:
                input()
            return

        if t['type'] == 'ID' and t['val'] == 'put':
            self.eat(); self.eat('LPAREN')
            self._out(self._display(self.expression()))
            self.eat('RPAREN'); return

        if t['type'] == 'ID' and t['val'] == 'write_file':
            self.eat(); self.eat('LPAREN')
            path = self.eat('STRING')['val']; self.eat('COMMA')
            val  = self.expression();         self.eat('RPAREN')
            with open(path, 'w', encoding='utf-8') as f: f.write(str(val))
            return

        if t['type'] == 'ID' and t['val'] == 'mem_write':
            self.eat(); self.eat('LPAREN')
            seg = self.eat('STRING')['val']; self.eat('COMMA')
            val = self.expression(); self.eat('RPAREN')
            try: _mem_write(seg, self._display(val))
            except Exception as e: self._out(f"[mem_write error] {e}")
            return

        if t['type'] == 'ID' and t['val'] == 'label':
            self.eat(); self.eat('LPAREN')
            name = self.expression()
            if self.match('COMMA'):
                self.eat()
                val = self._display(self.expression())
                while self.match('COMMA'):
                    self.eat(); self.expression()
                self.eat('RPAREN')
                key = f"_label_{name}"
                self.vars[key] = val
                self._out(f"[{name}] {val}")
            else:
                self.eat('RPAREN')
            return

        if t['type'] == 'ID' and t['val'] == 'txtbox':
            self.eat(); self.eat('LPAREN')
            lbl     = self._display(self.expression())
            varname = None
            if self.match('COMMA'):
                self.eat()
                nxt = self.peek()
                if nxt and nxt['type'] == 'STRING':
                    varname = self._display(self.expression())
            while self.match('COMMA'):
                self.eat(); self.expression()
            self.eat('RPAREN')
            body = self._block()
            if lbl:
                print(lbl)
            if varname:
                self.vars[varname] = self._ask(lbl + ": " if lbl else "")
            self._exec_block(body)
            return

        if t['type'] == 'ID' and t['val'] == 'when':
            self.eat(); self.eat('LPAREN')
            cond_toks = self._collect_until('RPAREN'); self.eat('RPAREN')
            body = self._block()
            branches = [(cond_toks, body)]
            while self.match('ID') and self.peek()['val'] in ('whenwise', 'ww'):
                self.eat(); self.eat('LPAREN')
                ec = self._collect_until('RPAREN'); self.eat('RPAREN')
                eb = self._block()
                branches.append((ec, eb))
            other = None
            if self.match('ID') and self.peek()['val'] in ('otherwise', 'else'):
                self.eat(); other = self._block()
            for bc, bb in branches:
                if self._truthy(self._eval_tokens(bc)):
                    self._exec_block(bb); return
            if other is not None: self._exec_block(other)
            return

        if t['type'] == 'ID' and t['val'] == 'while':
            self.eat(); self.eat('LPAREN')
            cond_toks = self._collect_until('RPAREN')
            # check for optional delay: while(cond, ms)
            delay_ms = None
            # scan cond_toks for a trailing comma + integer
            for i in range(len(cond_toks) - 1, -1, -1):
                if cond_toks[i]['type'] == 'COMMA':
                    maybe = cond_toks[i+1:]
                    if len(maybe) == 1 and maybe[0]['type'] == 'INT':
                        delay_ms = int(maybe[0]['val'])
                        cond_toks = cond_toks[:i]
                    break
            if delay_ms is None:
                cond_str = ' '.join(str(tok['val']) for tok in cond_toks)
                raise SyntaxError(
                    f"while loop requires a delay: use while({cond_str}, ms) — "
                    f"e.g. while({cond_str}, 100). "
                    f"A delay prevents the app from freezing."
                )
            self.eat('RPAREN')
            body = self._block()
            import time as _time
            while True:
                if not self._truthy(self._eval_tokens(cond_toks)): break
                try: self._exec_block(body)
                except BreakSignal: break
                if delay_ms is not None:
                    _time.sleep(delay_ms / 1000.0)
            return

        if t['type'] == 'ID' and t['val'] == 'repeat':
            self.eat()
            n    = int(self._coerce_int(self.expression()))
            body = self._block()
            for _ in range(n):
                try: self._exec_block(body)
                except BreakSignal: break
            return

        if t['type'] == 'ID':
            name = t['val']
            nxt  = self.peek(1)
            if nxt and nxt['type'] == 'LBRACK':
                self.eat(); self.eat()
                idx = self.expression(); self.eat('RBRACK'); self.eat('EQ')
                val = self.expression()
                arr = self.vars.get(name, [])
                if isinstance(arr, list):
                    i = int(self._coerce_int(idx))
                    while len(arr) <= i: arr.append("")
                    arr[i] = val; self.vars[name] = arr
                return
            if nxt and nxt['type'] == 'EQ':
                self.eat(); self.eat()
                self.vars[name] = self.expression(); return
            self.expression(); return

        self.expression()

    # -- block helpers ---------------------------------------------------------

    def _rhs(self):
        if self.match('LBRACK'):
            self.eat(); items = []
            while self.peek() and not self.match('RBRACK'):
                items.append(self.expression())
                if self.match('COMMA'): self.eat()
            self.eat('RBRACK'); return items
        return self.expression()

    _BLOCK_TERMINATORS = {'whenwise', 'ww', 'otherwise', 'else'}

    def _block(self):
        self.eat('LBRACE'); start = self.pos; depth = 1
        while self.pos < len(self.tokens) and depth:
            k = self.tokens[self.pos]['type']
            if k == 'LBRACE': depth += 1
            elif k == 'RBRACE': depth -= 1
            self.pos += 1
        return self.tokens[start: self.pos - 1]

    def _exec_block(self, toks):
        saved_tokens, saved_pos = self.tokens, self.pos
        self.tokens, self.pos = toks, 0
        try: self.run_all()
        finally: self.tokens, self.pos = saved_tokens, saved_pos

    def _collect_until(self, stop_type):
        toks = []; depth = 0
        while self.pos < len(self.tokens):
            t = self.tokens[self.pos]
            if t['type'] == 'LPAREN': depth += 1
            elif t['type'] == 'RPAREN':
                if depth == 0: break
                depth -= 1
            toks.append(t); self.pos += 1
        return toks

    def _eval_tokens(self, toks):
        saved_tokens, saved_pos = self.tokens, self.pos
        self.tokens, self.pos = toks, 0
        try: return self.expression()
        finally: self.tokens, self.pos = saved_tokens, saved_pos

    # -- expression parser -----------------------------------------------------

    _PREC = [
        {'==', '!=', '<', '>', '<=', '>='},
        {'+', '-'},
        {'*', '/', '%'},
    ]

    def expression(self):
        return self._prec(0)

    def _prec(self, lvl):
        if lvl >= len(self._PREC): return self._unary()
        ops  = self._PREC[lvl]
        left = self._prec(lvl + 1)
        while self.match('OP') and self.peek()['val'] in ops:
            op    = self.eat()['val']
            right = self._prec(lvl + 1)
            left  = self._op(op, left, right)
        return left

    def _unary(self):
        if self.match('OP', '-'):
            self.eat(); v = self._primary()
            return -v if isinstance(v, (int, float)) else v
        return self._primary()

    def _primary(self):
        t = self.peek()
        if not t: return 0

        if t['type'] == 'LPAREN':
            self.eat(); v = self.expression(); self.eat('RPAREN'); return v
        if t['type'] == 'FLOAT':  self.eat(); return t['val']
        if t['type'] == 'INT':    self.eat(); return t['val']
        if t['type'] == 'STRING': self.eat(); return t['val']

        if t['type'] == 'ID':
            name = t['val']; self.eat()

            if self.match('LPAREN'):
                self.eat()
                def _arg():   return self.expression()
                def _arg2():  a = _arg(); self.eat('COMMA'); b = _arg(); return a, b
                def _close(): self.eat('RPAREN')

                if name == 'put':
                    v = _arg(); _close(); self._out(self._display(v)); return v
                if name == 'read_file':
                    path = self.eat('STRING')['val']; _close()
                    try:
                        with open(path, encoding='utf-8') as f: return f.read()
                    except Exception as e: return f"ERROR:{e}"
                if name in ('ask', 'input'):
                    prompt = '' if self.match('RPAREN') else self._display(_arg())
                    _close(); return self._ask(prompt)
                if name == 'mem_read':
                    seg = self.eat('STRING')['val']; _close()
                    return _mem_read(seg)
                if name == 'mem_write':
                    seg = self.eat('STRING')['val']; self.eat('COMMA')
                    v = _arg(); _close()
                    try: _mem_write(seg, self._display(v))
                    except Exception as e: self._out(f"[mem_write error] {e}")
                    return v
                if name == 'len':
                    v = _arg(); _close()
                    return len(v) if isinstance(v, (str, list)) else 0
                if name == 'int':
                    v = _arg(); _close(); return self._coerce_int(v)
                if name == 'float':
                    v = _arg(); _close(); return self._coerce_float(v)
                if name == 'str':
                    v = _arg(); _close(); return self._display(v)
                if name == 'abs':
                    v = _arg(); _close()
                    return abs(v) if isinstance(v, (int, float)) else v
                if name == 'max':
                    a, b = _arg2(); _close(); return max(a, b)
                if name == 'cpu':
                    _close()
                    try:
                        import psutil
                        return 'CPU: {:.1f}%'.format(psutil.cpu_percent(interval=0.1))
                    except ImportError:
                        return 'CPU: N/A (pip install psutil)'
                if name == 'ram':
                    _close()
                    try:
                        import psutil
                        m = psutil.virtual_memory()
                        used  = m.used  / 1073741824
                        total = m.total / 1073741824
                        return 'RAM: {:.1f} GB / {:.1f} GB ({:.0f}%)'.format(used, total, m.percent)
                    except ImportError:
                        return 'RAM: N/A (pip install psutil)'
                if name == 'gpu':
                    _close()
                    try:
                        import subprocess, re as _re
                        out = subprocess.check_output(
                            ['nvidia-smi', '--query-gpu=utilization.gpu',
                             '--format=csv,noheader,nounits'],
                            stderr=subprocess.DEVNULL).decode().strip()
                        return 'GPU: {}%'.format(out.split('\n')[0].strip())
                    except Exception:
                        return 'GPU: N/A'
                if name == 'gpu_val':
                    _close()
                    try:
                        import subprocess
                        out = subprocess.check_output(
                            ['nvidia-smi', '--query-gpu=utilization.gpu',
                             '--format=csv,noheader,nounits'],
                            stderr=subprocess.DEVNULL).decode().strip()
                        return float(out.split('\n')[0].strip())
                    except Exception:
                        return -1.0
                if name == 'cpu_val':
                    _close()
                    try:
                        import psutil
                        return float(psutil.cpu_percent(interval=0.1))
                    except ImportError:
                        return -1.0
                if name == 'ram_used':
                    _close()
                    try:
                        import psutil
                        return psutil.virtual_memory().used / 1073741824
                    except ImportError:
                        return -1.0
                if name == 'ram_total':
                    _close()
                    try:
                        import psutil
                        return psutil.virtual_memory().total / 1073741824
                    except ImportError:
                        return -1.0
                if name == 'all_pc':
                    _close()
                    try:
                        import psutil
                        c = psutil.cpu_percent(interval=0.1)
                        m = psutil.virtual_memory()
                        used  = m.used  / 1073741824
                        total = m.total / 1073741824
                        cpu_s = 'CPU: {:.1f}%'.format(c)
                        ram_s = 'RAM: {:.1f}/{:.1f} GB'.format(used, total)
                        return '{} | {} | GPU: N/A'.format(cpu_s, ram_s)
                    except ImportError:
                        return 'N/A (pip install psutil)'
                if name == 'rand':
                    # Peek: is it range form rand(lo - hi)?
                    # Tokens at current pos would be: INT/FLOAT, OP(-), INT/FLOAT, RPAREN
                    saved = self.pos
                    try:
                        t1 = self.peek(0)
                        t2 = self.peek(1)
                        t3 = self.peek(2)
                        t4 = self.peek(3)
                        if (t1 and t1['type'] in ('INT','FLOAT') and
                            t2 and t2['type'] == 'OP' and t2['val'] == '-' and
                            t3 and t3['type'] in ('INT','FLOAT') and
                            t4 and t4['type'] == 'RPAREN'):
                            lo = int(self.eat()['val'])
                            self.eat('OP', '-')
                            hi = int(self.eat()['val'])
                            _close()
                            return _rng.randint(lo, hi)
                    except Exception:
                        self.pos = saved
                    # pick-list form: rand(a, b, c, ...)
                    choices = []
                    while not self.match('RPAREN') and self.peek():
                        choices.append(_arg())
                        if self.match('COMMA'): self.eat()
                    _close()
                    return _rng.choice(choices) if choices else 0
                if name == 'min':
                    a, b = _arg2(); _close(); return min(a, b)
                if name == 'type':
                    v = _arg(); _close()
                    if isinstance(v, list):  return "array"
                    if isinstance(v, float): return "float"
                    if isinstance(v, int):   return "int"
                    return "string"
                if name == 'write_file':
                    path = self.eat('STRING')['val']; self.eat('COMMA')
                    v = _arg(); _close()
                    with open(path, 'w', encoding='utf-8') as f: f.write(str(v))
                    return v
                while not self.match('RPAREN') and self.peek():
                    _arg()
                    if self.match('COMMA'): self.eat()
                _close(); return 0

            if self.match('LBRACK'):
                self.eat(); idx = self.expression(); self.eat('RBRACK')
                arr = self.vars.get(name, [])
                if isinstance(arr, list):
                    i = int(self._coerce_int(idx))
                    return arr[i] if 0 <= i < len(arr) else ""
                return ""

            return self.vars.get(name, 0)

        return 0

    # -- operators -------------------------------------------------------------

    def _op(self, op, l, r):
        if op == '+' and (isinstance(l, str) or isinstance(r, str)):
            return self._display(l) + self._display(r)
        if op == '+':  return l + r
        if op == '-':  return l - r
        if op == '*':  return l * r
        if op == '/':
            if r == 0: return 0
            res = l / r; return int(res) if res == int(res) else res
        if op == '%':  return int(l) % int(r)
        if op == '==': return int(l == r)
        if op == '!=': return int(l != r)
        if op == '<':  return int(l <  r)
        if op == '>':  return int(l >  r)
        if op == '<=': return int(l <= r)
        if op == '>=': return int(l >= r)
        return 0

    # -- helpers ---------------------------------------------------------------

    def _truthy(self, v):
        if isinstance(v, (int, float)): return v != 0
        if isinstance(v, str):          return v != ""
        if isinstance(v, list):         return len(v) > 0
        return bool(v)

    def _display(self, v):
        if isinstance(v, float) and v == int(v): return str(int(v))
        if isinstance(v, list): return "[" + ", ".join(self._display(x) for x in v) + "]"
        return str(v)

    def _coerce_int(self, v):
        try: return int(float(str(v)))
        except: return 0

    def _coerce_float(self, v):
        try: return float(str(v))
        except: return 0.0


# --- public runner ------------------------------------------------------------

def run_nova(source, output_fn=None, ask_fn=None):
    expanded = preprocess(source)
    braced   = _inject_braces(expanded)
    tokens   = tokenize(braced)
    interp   = NovaInterpreter(tokens, output_fn, ask_fn)
    interp.run_all()
    return interp


# --- REPL ---------------------------------------------------------------------

def _repl():
    """Simple terminal REPL: type Nova code line by line."""
    print("Nova Interpreter  -  type Nova code, blank line to run, Ctrl+C to exit")
    vars_ = {}
    buf   = []
    try:
        while True:
            try:
                line = input(">>> " if not buf else "... ")
            except EOFError:
                break
            if line.strip() == "" and buf:
                code = "\n".join(buf); buf = []
                try:
                    tokens = tokenize(_inject_braces(preprocess(code)))
                    interp = NovaInterpreter(tokens)
                    interp.vars = vars_
                    interp.run_all()
                    vars_ = interp.vars
                except Exception as e:
                    print(f"[Error] {e}")
            elif line.strip():
                buf.append(line)
    except KeyboardInterrupt:
        print()


# --- entry point --------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as f: src = f.read()
        run_nova(src)
    else:
        _repl()
