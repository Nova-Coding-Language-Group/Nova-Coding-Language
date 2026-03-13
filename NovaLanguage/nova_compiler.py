#!/usr/bin/env python3
"""
nova_compiler.py  -  Nova -> IL -> .exe

Features:
  button_grid(labelsArr, xsArr, ysArr, w, h, "Prefix")
      Compile-time unroll: creates one button per array element.
  on_button("Prefix", N) { ... }
      Defines the handler body for button Prefix_N.
  Multi-statement lines: separate statements with two or more spaces.
      e.g.  numA = 0  numB = 0  opCode = 0
  Inline blocks: { stmt  stmt }  on the same line.
  whenwise(expr) / ww(expr)
      elif-style branch after a when block.
  else / otherwise
      else-block after when/whenwise chain (both spellings work).
  ask("prompt")
      Read a line from Console.ReadLine() -> string.
      In a NovaUI txtbox block, reads from a GUI text input instead.
  txtbox("label") { ask("prompt") }
      Shows a labelled text-input box in the NovaUI window.
      The ask() inside captures the typed text.
  mem_write(name, val)
      Write a string into a named Windows shared memory block.
  mem_read(name)
      Read a string from a named Windows shared memory block.
      Both mem_write/mem_read use MemoryMappedFile so Python's
      multiprocessing.shared_memory can read/write the same block.
"""

import os, re, shutil, subprocess
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox


# --- ilasm finder -------------------------------------------------------------

def find_ilasm_path():
    windir = os.environ.get("WINDIR", r"C:\Windows")
    base   = os.path.join(windir, "Microsoft.NET")
    for fw in ("Framework64", "Framework"):
        fwbase = os.path.join(base, fw)
        if not os.path.isdir(fwbase): continue
        for v in sorted([d for d in os.listdir(fwbase) if d.startswith("v")], reverse=True):
            p = os.path.join(fwbase, v, "ilasm.exe")
            if os.path.isfile(p): return p
    return None


def escape_il(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')


# --- source pre-processor -----------------------------------------------------

def _strip_comment(s):
    """Remove // comment from a line, respecting string literals."""
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
    """Split on 2+ spaces outside parens/brackets/quotes.
    Lines starting with 'have' or 'use' are never split."""
    st = s.strip()
    if st.startswith('use '): return [st]
    if st.startswith('have '):
        parts = re.split(r'  +(?=have )', st)
        return [p.strip() for p in parts if p.strip()]
    parts, cur = [], ''
    depth_p = depth_b = 0
    in_str = False
    i = 0
    while i < len(s):
        c = s[i]
        if   c == '"' and not in_str: in_str = True;  cur += c
        elif c == '"' and in_str:     in_str = False; cur += c
        elif not in_str and c == '(': depth_p += 1; cur += c
        elif not in_str and c == ')': depth_p -= 1; cur += c
        elif not in_str and c == '[': depth_b += 1; cur += c
        elif not in_str and c == ']': depth_b -= 1; cur += c
        elif not in_str and depth_p == 0 and depth_b == 0 and c == ';':
            tok = cur.strip()
            if tok: parts.append(tok)
            cur = ''
        elif (not in_str and depth_p == 0 and depth_b == 0
              and c == ' ' and i+1 < len(s) and s[i+1] == ' '):
            tok = cur.strip()
            if tok: parts.append(tok)
            cur = ''
            while i < len(s) and s[i] == ' ': i += 1
            continue
        else: cur += c
        i += 1
    tok = cur.strip()
    if tok: parts.append(tok)
    return parts or [s.strip()]


def _expand_line(s, base_indent):
    """Expand inline { } blocks and multi-statement double-spaces."""
    if '{' not in s and '}' not in s:
        return [' ' * base_indent + p for p in _split_stmts(s) if p.strip()]
    result = []
    cur = ''
    depth = 0
    in_str = False
    depth_p = 0
    for ch in s:
        if   ch == '"' and not in_str: in_str = True;  cur += ch
        elif ch == '"' and in_str:     in_str = False; cur += ch
        elif not in_str and ch == '(': depth_p += 1; cur += ch
        elif not in_str and ch == ')': depth_p -= 1; cur += ch
        elif not in_str and depth_p == 0 and ch == '{':
            for sub in _split_stmts(cur):
                if sub.strip(): result.append(' ' * (base_indent + depth*4) + sub.strip())
            cur = ''; depth += 1
        elif not in_str and depth_p == 0 and ch == '}':
            for sub in _split_stmts(cur):
                if sub.strip(): result.append(' ' * (base_indent + depth*4) + sub.strip())
            cur = ''; depth = max(0, depth-1)
        else: cur += ch
    for sub in _split_stmts(cur):
        if sub.strip(): result.append(' ' * base_indent + sub.strip())
    return result


def preprocess(nova_text):
    """Return list of indented lines ready for the compiler."""
    lines = []
    for ln in nova_text.splitlines():
        stripped = ln.strip()
        if not stripped or stripped.startswith('//'): continue
        indent = len(ln) - len(ln.lstrip())
        clean  = _strip_comment(stripped)
        if not clean: continue
        for el in _expand_line(clean, indent):
            if el.strip():
                lines.append(el)
    return lines


# --- IL emitter ---------------------------------------------------------------

class ILEmitter:
    def __init__(self, name="NovaProgram"):
        self.assembly      = name
        self.fields        = {}
        self.cctor         = []
        self.handlers      = {}
        self.handler_order = []
        self.has_novaui    = False
        self._lbl          = 0
        self._arrays       = {}

    def ulabel(self, base):
        self._lbl += 1
        return f"{base}_{self._lbl}"

    def add_handler(self, name, body):
        if name not in self.handlers:
            self.handler_order.append(name)
        self.handlers[name] = body

    def get_il(self, main_lines):
        A = self.assembly
        il = [".assembly extern mscorlib {}"]
        il += [f".assembly {A} {{}}", f".module {A}.exe\n",
               f".class public auto ansi beforefieldinit {A} extends [mscorlib]System.Object {{"]

        for n, t in self.fields.items():
            ft = ("int32"   if t == "int"   else
                  "float64" if t == "float" else
                  "string"  if t == "string" else "class [mscorlib]System.String[]")
            il.append(f"  .field public static {ft} {n}")

        if self.has_novaui:
            il.insert(0, ".assembly extern NovaUI {}")

            il.append(f"""\
  .method public hidebysig static void _CreateWindow(string, int32, int32) cil managed {{
    .maxstack 3
    ldarg.0
    ldarg.1
    ldarg.2
    call void [NovaUI]NovaUI::ui_window(string, int32, int32, class [mscorlib]System.Action)
    ret
  }}

  .method public hidebysig static void _SetIcon(string) cil managed {{
    .maxstack 1
    ldarg.0
    call void [NovaUI]NovaUI::icon(string)
    ret
  }}

  .method public hidebysig static void _ShowMessage(string) cil managed {{
    .maxstack 1
    ldarg.0
    call void [NovaUI]NovaUI::popup(string)
    ret
  }}

  .method public hidebysig static void _ExitApp() cil managed {{
    .maxstack 1
    ldc.i4.0
    call void [mscorlib]System.Environment::Exit(int32)
    ret
  }}

  .method public hidebysig static void _SetLabelText(string, string) cil managed {{
    .maxstack 2
    ldarg.0
    ldarg.1
    call void [NovaUI]NovaUI::_SetLabelTextByName(string, string)
    ret
  }}

  .method public hidebysig static int32 _AddNamedLabelRet(string, string, int32, int32, int32, int32) cil managed {{
    .maxstack 8
    .locals init (int32 V_0)
    ldarg.1
    ldarg.2
    ldarg.3
    ldarg 4
    ldarg 5
    call int32 [NovaUI]NovaUI::label(string, int32, int32, int32, int32)
    stloc.0
    ldarg.0
    ldloc.0
    call void [NovaUI]NovaUI::_RegisterLabelName(string, int32)
    ldloc.0
    ret
  }}

  .method public hidebysig static void _AddNamedLabel(string, string, int32, int32, int32, int32) cil managed {{
    .maxstack 8
    .locals init (int32 V_0)
    ldarg.0
    ldarg.1
    ldarg.2
    ldarg.3
    ldarg 4
    ldarg 5
    call int32 {A}::_AddNamedLabelRet(string, string, int32, int32, int32, int32)
    pop
    ret
  }}

""")
            il.append(f"""\
  .method public hidebysig static int32 _AddButton(int32, int32, int32, int32, string, string) cil managed {{
    .maxstack 8
    ldarg 4
    ldarg.0
    ldarg.1
    ldnull
    call void [NovaUI]NovaUI::button(string, int32, int32, class [mscorlib]System.Action)
    ldc.i4.0
    ret
  }}

  .method public hidebysig static int32 _AddButtonTagged(int32, int32, int32, int32, string, string, int32) cil managed {{
    .maxstack 8
    ldarg 4
    ldarg.0
    ldarg.1
    ldnull
    call void [NovaUI]NovaUI::button(string, int32, int32, class [mscorlib]System.Action)
    ldc.i4.0
    ret
  }}

""")
            il.append(f"""\
  .method public hidebysig static void _Dispatch() cil managed {{
    .maxstack 0
    ret
  }}

""")
            il.append(f"""\
  .method public hidebysig static void _RegisterLabelName(string, int32) cil managed {{
    .maxstack 2
    ldarg.0
    ldarg.1
    call void [NovaUI]NovaUI::_RegisterLabelName(string, int32)
    ret
  }}

  .method public hidebysig static int32 _GetLabelId(string) cil managed {{
    .maxstack 1
    ldarg.0
    call int32 [NovaUI]NovaUI::_GetLabelId(string)
    ret
  }}

  .method public hidebysig static void _SetLabelTextByName(string, string) cil managed {{
    .maxstack 2
    ldarg.0
    ldarg.1
    call void [NovaUI]NovaUI::_SetLabelTextByName(string, string)
    ret
  }}

  .method public hidebysig static void _Noop() cil managed {{
    .maxstack 0
    ret
  }}

""")

        # --- ask (Console.ReadLine) wrapper -----------------------------------
        # txtbox blocks swap this out at runtime via a handler field, but for
        # console-mode Nova programs this just reads stdin.
        il.append(f"""\
  .method public hidebysig static string _ConsoleAsk(string) cil managed {{
    .maxstack 2
    ldarg.0
    call void [mscorlib]System.Console::Write(string)
    call string [mscorlib]System.Console::ReadLine()
    dup
    brtrue.s ASK_OK
    pop
    ldstr ""
  ASK_OK:
    ret
  }}

""")

        # --- mem_write / mem_read wrappers ------------------------------------
        # File-based IPC: writes to %TEMP%/nova_shm_<name>.txt
        # Python can interop with plain open() on the same path.
        il.append(f"""\
  .method public hidebysig static string _ShmPath(string) cil managed {{
    .maxstack 4
    call string [mscorlib]System.IO.Path::GetTempPath()
    ldstr "nova_shm_"
    ldarg.0
    ldstr ".txt"
    call string [mscorlib]System.String::Concat(string, string, string, string)
    call string [mscorlib]System.IO.Path::Combine(string, string)
    ret
  }}

  .method public hidebysig static void _MemWrite(string, string) cil managed {{
    .maxstack 3
    .try {{
      ldarg.0
      call string {A}::_ShmPath(string)
      ldarg.1
      call void [mscorlib]System.IO.File::WriteAllText(string, string)
      leave.s MWR_OK
    }}
    catch [mscorlib]System.Exception {{ pop leave.s MWR_OK }}
  MWR_OK:
    ret
  }}

  .method public hidebysig static string _MemRead(string) cil managed {{
    .maxstack 2
    .locals init (string V_path)
    ldarg.0
    call string {A}::_ShmPath(string)
    stloc V_path
    .try {{
      ldloc V_path
      call string [mscorlib]System.IO.File::ReadAllText(string)
      ret
    }}
    catch [mscorlib]System.Exception {{ pop ldstr "" ret }}
  }}

""")

        il += ["\n  .method private hidebysig specialname rtspecialname static void .cctor() cil managed {",
               "    .maxstack 10"]
        for ln in self.cctor: il.append("    " + ln)
        il.append("    ret\n  }\n")

        il += ["  .method public hidebysig static void Main() cil managed {",
               "    .entrypoint", "    .maxstack 8", f"    call void {A}::StartApp()", "    ret\n  }",
               f"\n  .method public hidebysig static void StartApp() cil managed {{",
               "    .maxstack 64"]
        for ln in main_lines: il.append("    " + ln)
        il.append("    ret\n  }\n")

        for hname in self.handler_order:
            body = self.handlers[hname]
            il += [f"  .method public hidebysig static void {hname}() cil managed {{",
                   "    .maxstack 64"]
            for ln in body: il.append("    " + ln)
            il.append("    ret\n  }\n")

        il.append("}")
        return "\n".join(il)


# --- expression parser --------------------------------------------------------
# Full precedence-climbing parser matching interpreter feature set.

_EXPR_TOK = re.compile(
    r'(?P<FLOAT>\d+\.\d+)'
    r'|(?P<INT>\d+)'
    r'|(?P<STR>"[^"]*")'
    r'|(?P<OP>==|!=|<=|>=|[+\-*/%<>])'
    r'|(?P<ID>[A-Za-z_]\w*)'
    r'|(?P<LP>\()|(?P<RP>\))'
    r'|(?P<LB>\[)|(?P<RB>\])'
    r'|(?P<CM>,)'
    r'|(?P<WS>\s+)'
)

def _etokenise(s):
    toks = []
    for m in _EXPR_TOK.finditer(s):
        k = m.lastgroup
        if k == 'WS': continue
        toks.append((k, m.group()))
    return toks

_PREC_LEVELS = [
    {'==', '!=', '<', '>', '<=', '>='},
    {'+', '-'},
    {'*', '/', '%'},
]

def make_parser(emitter, read_file_paths):
    A = emitter.assembly

    def ensure_int(name):
        if name not in emitter.fields:
            emitter.fields[name] = "int"
            emitter.cctor += ['ldc.i4.0', f'stsfld int32 {A}::{name}']

    def ensure_float(name):
        if name not in emitter.fields:
            emitter.fields[name] = "float"
            emitter.cctor += ['ldc.r8 0.0', f'stsfld float64 {A}::{name}']

    def il_type(t):
        return ("int32"   if t == "int"   else
                "float64" if t == "float" else
                "string"  if t == "string" else "class [mscorlib]System.String[]")

    def to_float(t, il):
        if t == "int": il.append('conv.r8')

    def to_string(t, il):
        if t == "int":
            il += ["box [mscorlib]System.Int32",
                   "callvirt instance string [mscorlib]System.Object::ToString()"]
        elif t == "float":
            il += ["box [mscorlib]System.Double",
                   "callvirt instance string [mscorlib]System.Object::ToString()"]

    def prec(toks, pos, il, lvl):
        if lvl >= len(_PREC_LEVELS): return unary(toks, pos, il)
        ops = _PREC_LEVELS[lvl]
        lt, pos = prec(toks, pos, il, lvl + 1)
        while pos < len(toks) and toks[pos][0] == 'OP' and toks[pos][1] in ops:
            op = toks[pos][1]; pos += 1
            ril = []; rt, pos = prec(toks, pos, ril, lvl + 1)

            if op in ('==', '!=', '<', '>', '<=', '>='):
                if lt == "float" or rt == "float":
                    to_float(lt, il); il += ril; to_float(rt, il)
                else:
                    il += ril
                il += {'=='  : ['ceq'],
                       '!='  : ['ceq', 'ldc.i4.0', 'ceq'],
                       '<'   : ['clt'],
                       '>'   : ['cgt'],
                       '<='  : ['cgt', 'ldc.i4.0', 'ceq'],
                       '>='  : ['clt', 'ldc.i4.0', 'ceq']}[op]
                lt = "int"

            elif op == '+' and (lt == "string" or rt == "string"):
                to_string(lt, il); il += ril; to_string(rt, il)
                il.append('call string [mscorlib]System.String::Concat(string, string)')
                lt = "string"

            elif lt == "float" or rt == "float":
                to_float(lt, il); il += ril; to_float(rt, il)
                il.append({'+':'add', '-':'sub', '*':'mul', '/':'div', '%':'rem'}[op])
                lt = "float"

            else:
                if op == '/':
                    il.append('conv.r8'); to_float("int", ril); il += ril
                    il.append('div'); lt = "float"
                elif op == '%':
                    il += ril; il.append('rem'); lt = "int"
                else:
                    il += ril
                    il.append({'+':'add', '-':'sub', '*':'mul'}[op]); lt = "int"

        return lt, pos

    def unary(toks, pos, il):
        if pos < len(toks) and toks[pos] == ('OP', '-'):
            pos += 1; t, pos = primary(toks, pos, il)
            il.append('neg')
            return ("float" if t == "float" else "int"), pos
        return primary(toks, pos, il)

    def primary(toks, pos, il):
        if pos >= len(toks): il.append('ldc.i4.0'); return "int", pos
        k, v = toks[pos]

        if k == 'FLOAT': il.append(f'ldc.r8 {v}'); return "float", pos+1
        if k == 'INT':   il.append(f'ldc.i4 {v}'); return "int",   pos+1
        if k == 'STR':   il.append(f'ldstr "{escape_il(v[1:-1])}"'); return "string", pos+1

        if k == 'LP':
            pos += 1; t, pos = prec(toks, pos, il, 0)
            if pos < len(toks) and toks[pos][0] == 'RP': pos += 1
            return t, pos

        if k == 'ID':
            name = v; pos += 1

            if pos < len(toks) and toks[pos][0] == 'LP':
                pos += 1
                def one(out_il=il):
                    nonlocal pos
                    ail = []; at, pos = prec(toks, pos, ail, 0)
                    out_il += ail; return at
                def close():
                    nonlocal pos
                    if pos < len(toks) and toks[pos][0] == 'RP': pos += 1
                def comma():
                    nonlocal pos
                    if pos < len(toks) and toks[pos][0] == 'CM': pos += 1

                if name == 'read_file':
                    if pos < len(toks) and toks[pos][0] == 'STR':
                        path = toks[pos][1][1:-1]; pos += 1
                        read_file_paths.add(path)
                        il += [f'ldstr "{escape_il(path)}"',
                               'call string [mscorlib]System.IO.File::ReadAllText(string)']
                    close(); return "string", pos
                if name == 'len':
                    at = one(); close()
                    if at == "string[]":
                        il += ['ldlen', 'conv.i4']
                    else:
                        to_string(at, il)
                        il.append('callvirt instance int32 [mscorlib]System.String::get_Length()')
                    return "int", pos
                if name == 'int':
                    at = one(); close(); to_string(at, il)
                    il.append('call int32 [mscorlib]System.Int32::Parse(string)')
                    return "int", pos
                if name == 'float':
                    at = one(); close(); to_string(at, il)
                    il.append('call float64 [mscorlib]System.Double::Parse(string)')
                    return "float", pos
                if name == 'str':
                    at = one(); close(); to_string(at, il)
                    return "string", pos
                if name == 'abs':
                    at = one(); close()
                    if at == "float":
                        il.append('call float64 [mscorlib]System.Math::Abs(float64)')
                    else:
                        il.append('call int32 [mscorlib]System.Math::Abs(int32)')
                    return at, pos
                if name in ('max', 'min'):
                    at = one(); comma()
                    bt_il = []; bt, pos = prec(toks, pos, bt_il, 0); close()
                    if at == "float" or bt == "float":
                        to_float(at, il); il += bt_il; to_float(bt, il)
                        il.append(f'call float64 [mscorlib]System.Math::{"Max" if name=="max" else "Min"}(float64,float64)')
                        return "float", pos
                    else:
                        il += bt_il
                        il.append(f'call int32 [mscorlib]System.Math::{"Max" if name=="max" else "Min"}(int32,int32)')
                        return "int", pos
                if name == 'type':
                    ail = []; at, pos = prec(toks, pos, ail, 0); close()
                    ts = {"int":"int","float":"float","string":"string","string[]":"array"}.get(at,"string")
                    il.append(f'ldstr "{ts}"')
                    return "string", pos
                if name in ('ask', 'input'):
                    # emit: call {A}::_ConsoleAsk(string)
                    # consume the prompt argument
                    if pos < len(toks) and toks[pos][0] != 'RP':
                        pt = one()
                        to_string(pt, il)
                    else:
                        il.append('ldstr ""')
                    close()
                    il.append(f'call string {A}::_ConsoleAsk(string)')
                    return "string", pos
                if name == 'mem_read':
                    if pos < len(toks) and toks[pos][0] == 'STR':
                        seg = toks[pos][1][1:-1]; pos += 1
                    else:
                        seg = "nova_shm"
                    close()
                    il += [f'ldstr "{escape_il(seg)}"',
                           f'call string {A}::_MemRead(string)']
                    return "string", pos
                if name == 'mem_write':
                    if pos < len(toks) and toks[pos][0] == 'STR':
                        seg = toks[pos][1][1:-1]; pos += 1
                    else:
                        seg = "nova_shm"
                    comma()
                    vt = one(); close()
                    to_string(vt, il)
                    il.insert(len(il) - 1, f'ldstr "{escape_il(seg)}"')
                    il.append(f'call void {A}::_MemWrite(string, string)')
                    return vt, pos
                # unknown - skip args
                depth = 1
                while pos < len(toks) and depth:
                    if toks[pos][0] == 'LP': depth += 1
                    elif toks[pos][0] == 'RP': depth -= 1
                    pos += 1
                il.append('ldc.i4.0'); return "int", pos

            if pos < len(toks) and toks[pos][0] == 'LB':
                pos += 1
                idx_il = []; it, pos = prec(toks, pos, idx_il, 0)
                if pos < len(toks) and toks[pos][0] == 'RB': pos += 1
                if name not in emitter.fields: emitter.fields[name] = "string[]"
                il.append(f'ldsfld class [mscorlib]System.String[] {A}::{name}')
                il += idx_il
                if it == "float": il.append('conv.i4')
                il.append('ldelem.ref')
                return "string", pos

            if name not in emitter.fields:
                emitter.fields[name] = "string"
                emitter.cctor += ['ldstr ""', f'stsfld string {A}::{name}']
            t = emitter.fields[name]
            il.append(f'ldsfld {il_type(t)} {A}::{name}')
            return t, pos

        il.append('ldc.i4.0'); return "int", pos

    def parse(expr_str, out):
        toks = _etokenise(expr_str.strip())
        if not toks: out.append('ldc.i4.0'); return "int"
        t, _ = prec(toks, 0, out, 0)
        return t

    return parse, ensure_int


# --- block compiler -----------------------------------------------------------

def translate_nova_to_il(nova_text, assembly_name="NovaProgram"):
    lines           = preprocess(nova_text)
    emitter         = ILEmitter(assembly_name)
    read_file_paths = set()
    handler_counter = [0]
    parse, ensure_int = make_parser(emitter, read_file_paths)
    A = emitter.assembly

    # Pre-pass: scan for all variable assignments and register field TYPES only
    # (no cctor emissions — the main compiler handles initialization).
    # This ensures that when a variable is first READ inside a handler before
    # being assigned, the field type is already known so store() can coerce correctly.
    import re as _re
    for _line in lines:
        _s = _line.strip().rstrip('{').rstrip()
        _m = _re.match(r'^have\s+(\w+)\s*=\s*(.+)$', _s)
        if _m:
            _n, _expr = _m.group(1), _m.group(2).strip()
            if _n not in emitter.fields:
                if _re.match(r'^-?\d+\.\d+$', _expr):
                    emitter.fields[_n] = "float"
                elif _expr.lstrip('-').isdigit():
                    emitter.fields[_n] = "int"
                elif _expr.startswith('"'):
                    emitter.fields[_n] = "string"
                # else: leave unknown, main compiler will determine
            continue
        # plain assignments: don't pre-declare — main compiler handles these
        # correctly when it processes them in order

    def get_indent(line): return len(line) - len(line.lstrip())

    def store(name, t, il):
        ft = "int32" if t=="int" else "float64" if t=="float" else "string"
        il.append(f'stsfld {ft} {A}::{name}')

    def coerce_to_string(t, il):
        if t == "int":
            il += ["box [mscorlib]System.Int32",
                   "callvirt instance string [mscorlib]System.Object::ToString()"]
        elif t == "float":
            il += ["box [mscorlib]System.Double",
                   "callvirt instance string [mscorlib]System.Object::ToString()"]

    def compile_block(start, min_indent):
        il  = []
        idx = start
        while idx < len(lines):
            line   = lines[idx]
            indent = get_indent(line)
            if indent < min_indent: break
            s = line.strip().rstrip('{').rstrip()

            # use
            if re.match(r'^use\s+\w+', s):
                if 'novaui' in s: emitter.has_novaui = True
                idx += 1; continue

            # have
            m = re.match(r'^have\s+(\w+)\s*=\s*(.+)$', s)
            if m:
                name, val = m.group(1), m.group(2).strip()
                if val.startswith('['):
                    items = [x.strip().strip('"') for x in val[1:-1].split(',')]
                    emitter._arrays[name] = items
                    emitter.fields[name]  = "string[]"
                    emitter.cctor += [f'ldc.i4 {len(items)}',
                                      'newarr [mscorlib]System.String']
                    for i, it in enumerate(items):
                        emitter.cctor += ['dup', f'ldc.i4 {i}',
                                          f'ldstr "{escape_il(it)}"', 'stelem.ref']
                    emitter.cctor.append(f'stsfld class [mscorlib]System.String[] {A}::{name}')
                elif val.lstrip('-').isdigit():
                    emitter.fields[name] = "int"
                    emitter.cctor += [f'ldc.i4 {val}', f'stsfld int32 {A}::{name}']
                elif re.match(r'^-?\d+\.\d+$', val):
                    emitter.fields[name] = "float"
                    emitter.cctor += [f'ldc.r8 {val}', f'stsfld float64 {A}::{name}']
                elif val.startswith('"') and val.endswith('"'):
                    emitter.fields[name] = "string"
                    emitter.cctor += [f'ldstr "{escape_il(val[1:-1])}"',
                                      f'stsfld string {A}::{name}']
                else:
                    t = parse(val, [])
                    emitter.fields[name] = t
                    parse(val, emitter.cctor)
                    store(name, t, emitter.cctor)
                idx += 1; continue

            # colors(preset) { txt=#hex bg=#hex accent=#hex }
            # colours(...) is accepted as an alias.
            if re.match(r'^colou?rs\s*\(', s):
                emitter.has_novaui = True
                pm = re.match(r'^colou?rs\s*\(\s*"?([^")]*)"?\s*\)', s)
                preset = pm.group(1).strip() if pm else ""
                inner_lines = []
                tmp_idx = idx + 1
                while tmp_idx < len(lines):
                    ln = lines[tmp_idx].strip()
                    if get_indent(lines[tmp_idx]) <= indent and ln not in ('', '{', '}'):
                        break
                    if ln and ln not in ('{', '}'):
                        inner_lines.append(ln)
                    tmp_idx += 1
                idx = tmp_idx
                if preset:
                    il.append(f'ldstr "{escape_il(preset)}"\n    call void [NovaUI]NovaUI::_apply_preset(string)')
                for ln in inner_lines:
                    cm = re.match(r'(bg|txt|text|accent)\s*=\s*(#[0-9A-Fa-f]{6})', ln)
                    if cm:
                        key, val = cm.group(1), cm.group(2)
                        if key == 'bg':
                            il += [f'ldstr "{escape_il(val)}"',
                                   'call void [NovaUI]NovaUI::set_bg(string)']
                        elif key in ('txt', 'text'):
                            il += [f'ldstr "{escape_il(val)}"',
                                   'call void [NovaUI]NovaUI::set_text(string)']
                        elif key == 'accent':
                            il += [f'ldstr "{escape_il(val)}"',
                                   'call void [NovaUI]NovaUI::set_accent(string)']
                continue

            # ui_window("title", w, h) { }
            # ui_window("title", w, h, #accent) { }
            # ui_window("title", w, h, #bg, #accent, #text) { }
            m1 = re.match(r'^ui_window\s*\(\s*"([^"]+)"\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(#[0-9A-Fa-f]{6})\s*,\s*(#[0-9A-Fa-f]{6})\s*,\s*(#[0-9A-Fa-f]{6})\s*\)$', s)
            m2 = re.match(r'^ui_window\s*\(\s*"([^"]+)"\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*(#[0-9A-Fa-f]{6}))?\s*\)$', s) if not m1 else None
            m = m1 or m2
            if m:
                emitter.has_novaui = True
                inner, idx = compile_block(idx+1, indent+1)
                body_name = "_WindowBody_" + emitter.ulabel('WB')
                emitter.add_handler(body_name, inner)
                action_il = ['ldnull',
                             'ldftn void ' + A + '::' + body_name + '()',
                             'newobj instance void [mscorlib]System.Action::.ctor(object, native int)']
                if m1:
                    title, w, h = m1.group(1), m1.group(2), m1.group(3)
                    bg, acc, txt = m1.group(4), m1.group(5), m1.group(6)
                    il += ['ldstr "' + escape_il(title) + '"',
                           'ldc.i4 ' + w, 'ldc.i4 ' + h,
                           'ldstr "' + escape_il(bg) + '"',
                           'ldstr "' + escape_il(acc) + '"',
                           'ldstr "' + escape_il(txt) + '"'] + action_il + [
                           'call void [NovaUI]NovaUI::ui_window(string, int32, int32, string, string, string, class [mscorlib]System.Action)']
                elif m2.group(4):
                    title, w, h, acc = m2.group(1), m2.group(2), m2.group(3), m2.group(4)
                    il += ['ldstr "' + escape_il(title) + '"',
                           'ldc.i4 ' + w, 'ldc.i4 ' + h,
                           'ldstr "' + escape_il(acc) + '"'] + action_il + [
                           'call void [NovaUI]NovaUI::ui_window(string, int32, int32, string, class [mscorlib]System.Action)']
                else:
                    title, w, h = m2.group(1), m2.group(2), m2.group(3)
                    il += ['ldstr "' + escape_il(title) + '"',
                           'ldc.i4 ' + w, 'ldc.i4 ' + h] + action_il + [
                           'call void [NovaUI]NovaUI::ui_window(string, int32, int32, class [mscorlib]System.Action)']
                continue

            # button
            m = re.match(r'^button\s*\(\s*"([^"]+)"\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*(\d+)\s*,\s*(\d+)\s*)?(?:,\s*(#[0-9A-Fa-f]{6}))?\s*\)$', s)
            if m:
                txt, x, y = m.group(1), m.group(2), m.group(3)
                bw     = m.group(4) or '120'
                bh     = m.group(5) or '30'
                hexcol = m.group(6)
                hname  = "H_" + str(handler_counter[0]); handler_counter[0] += 1
                inner, idx = compile_block(idx+1, indent+1)
                emitter.add_handler(hname, inner)
                if hexcol:
                    il += ['ldstr "' + escape_il(txt) + '"',
                           'ldc.i4 ' + x, 'ldc.i4 ' + y, 'ldc.i4 ' + bw, 'ldc.i4 ' + bh,
                           'ldstr "' + escape_il(hexcol) + '"',
                           'ldnull',
                           'ldftn void ' + A + '::' + hname + '()',
                           'newobj instance void [mscorlib]System.Action::.ctor(object, native int)',
                           'call void [NovaUI]NovaUI::button(string, int32, int32, int32, int32, string, class [mscorlib]System.Action)']
                else:
                    il += ['ldstr "' + escape_il(txt) + '"',
                           'ldc.i4 ' + x, 'ldc.i4 ' + y, 'ldc.i4 ' + bw, 'ldc.i4 ' + bh,
                           'ldnull',
                           'ldftn void ' + A + '::' + hname + '()',
                           'newobj instance void [mscorlib]System.Action::.ctor(object, native int)',
                           'call void [NovaUI]NovaUI::button(string, int32, int32, int32, int32, class [mscorlib]System.Action)']
                continue

            # button_grid
            m = re.match(r'^button_grid\s*\(\s*(\w+)\s*,\s*(\w+)\s*,\s*(\w+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*"([^"]+)"\s*\)$', s)
            if m:
                lv, xv, yv, bw, bh, prefix = m.groups()
                labels = emitter._arrays.get(lv, [])
                xs     = emitter._arrays.get(xv, [])
                ys     = emitter._arrays.get(yv, [])
                for i, lbl in enumerate(labels):
                    hname = prefix + "_" + str(i)
                    if hname not in emitter.handlers:
                        emitter.add_handler(hname, [])
                    il += ['ldstr "' + escape_il(lbl) + '"',
                           'ldc.i4 ' + str(xs[i]),
                           'ldc.i4 ' + str(ys[i]),
                           'ldc.i4 ' + bw,
                           'ldc.i4 ' + bh,
                           'ldnull',
                           'ldftn void ' + A + '::' + hname + '()',
                           'newobj instance void [mscorlib]System.Action::.ctor(object, native int)',
                           'call void [NovaUI]NovaUI::button(string, int32, int32, int32, int32, class [mscorlib]System.Action)']
                idx += 1; continue

            # on_button
            m = re.match(r'^on_button\s*\(\s*"([^"]+)"\s*,\s*(\d+)\s*\)$', s)
            if m:
                prefix, bidx = m.groups()
                hname = prefix + "_" + bidx
                inner, idx = compile_block(idx+1, indent+1)
                emitter.add_handler(hname, inner)
                continue

            # named_label
            m = re.match(r'^named_label\s*\(\s*"([^"]+)"\s*,\s*"([^"]*)"\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)$', s)
            if m:
                lid, txt, x, y, w, h = m.groups()
                il += [f'ldstr "{escape_il(lid)}"', f'ldstr "{escape_il(txt)}"',
                       f'ldc.i4 {x}', f'ldc.i4 {y}', f'ldc.i4 {w}', f'ldc.i4 {h}',
                       f'call void {A}::_AddNamedLabel(string, string, int32, int32, int32, int32)']
                idx += 1; continue

            # set_label
            m = re.match(r'^set_label\s*\(\s*"([^"]+)"\s*,\s*(.+)\s*\)$', s)
            if m:
                lid, expr = m.group(1), m.group(2).strip()
                il.append(f'ldstr "{escape_il(lid)}"')
                t = parse(expr, il)
                coerce_to_string(t, il)
                il.append(f'call void {A}::_SetLabelText(string, string)')
                idx += 1; continue

            # label("name", expr) — 2-arg: create-or-update a named label at auto position
            m2 = re.match(r'^label\s*\(\s*"([^"]+)"\s*,\s*(.+)\s*\)$', s)
            if m2 and not re.match(r'^label\s*\(\s*"[^"]+"\s*,\s*\d+', s):
                lid, expr = m2.group(1), m2.group(2).strip()
                # Register the label name as a field so we can track whether it exists.
                # We always call _SetLabelTextByName; the first time this runs the label
                # won't exist yet so we auto-create it at a sensible default position,
                # then on subsequent calls we just update the text.
                lfield = "_lbl_exists_" + escape_il(lid).replace('"','').replace(' ','_')
                if lfield not in emitter.fields:
                    emitter.fields[lfield] = "int"
                    emitter.cctor += ['ldc.i4.0', f'stsfld int32 {A}::{lfield}']
                auto_id = escape_il(lid)
                # Emit: if not exists, create; then always set text
                skip_create = emitter.ulabel("LC")
                il += [f'ldsfld int32 {A}::{lfield}',
                       f'brtrue.s {skip_create}',
                       f'ldstr "{auto_id}"',
                       f'ldstr ""',
                       'ldc.i4 20', 'ldc.i4 60', 'ldc.i4 340', 'ldc.i4 24',
                       f'call void {A}::_AddNamedLabel(string, string, int32, int32, int32, int32)',
                       'ldc.i4.1', f'stsfld int32 {A}::{lfield}',
                       f'{skip_create}:',
                       f'ldstr "{auto_id}"']
                t = parse(expr, il)
                coerce_to_string(t, il)
                il.append(f'call void {A}::_SetLabelText(string, string)')
                idx += 1; continue

            # label (3-arg or 5-arg, optional colour)
            m3 = re.match(r'^label\s*\(\s*"([^"]+)"\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*(#[0-9A-Fa-f]{6}))?\s*\)$', s)
            if m3:
                txt, x, y, hexcol = m3.group(1), m3.group(2), m3.group(3), m3.group(4)
                auto_id = escape_il("_lbl_" + emitter.ulabel('L'))
                if hexcol:
                    il += ['ldstr "' + auto_id + '"', 'ldstr "' + escape_il(txt) + '"',
                           'ldc.i4 ' + x, 'ldc.i4 ' + y, 'ldc.i4 200', 'ldc.i4 24',
                           'ldstr "' + escape_il(hexcol) + '"',
                           'call int32 [NovaUI]NovaUI::label(string, int32, int32, int32, int32, string)',
                           'call void [NovaUI]NovaUI::_RegisterLabelName(string, int32)']
                else:
                    il += ['ldstr "' + auto_id + '"', 'ldstr "' + escape_il(txt) + '"',
                           'ldc.i4 ' + x, 'ldc.i4 ' + y, 'ldc.i4 200', 'ldc.i4 24',
                           'call void ' + A + '::_AddNamedLabel(string, string, int32, int32, int32, int32)']
                idx += 1; continue
            m5 = re.match(r'^label\s*\(\s*"([^"]+)"\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*(#[0-9A-Fa-f]{6}))?\s*\)$', s)
            if m5:
                txt, x, y, w, h, hexcol = m5.groups()
                auto_id = escape_il("_lbl_" + emitter.ulabel('L'))
                if hexcol:
                    il += ['ldstr "' + auto_id + '"', 'ldstr "' + escape_il(txt) + '"',
                           'ldc.i4 ' + x, 'ldc.i4 ' + y, 'ldc.i4 ' + w, 'ldc.i4 ' + h,
                           'ldstr "' + escape_il(hexcol) + '"',
                           'call int32 [NovaUI]NovaUI::label(string, int32, int32, int32, int32, string)',
                           'call void [NovaUI]NovaUI::_RegisterLabelName(string, int32)']
                else:
                    il += ['ldstr "' + auto_id + '"', 'ldstr "' + escape_il(txt) + '"',
                           'ldc.i4 ' + x, 'ldc.i4 ' + y, 'ldc.i4 ' + w, 'ldc.i4 ' + h,
                           'call void ' + A + '::_AddNamedLabel(string, string, int32, int32, int32, int32)']
                idx += 1; continue

            # txtbox("label", "varname") { ... }  or  txtbox("label") { ... }
            # Creates a text input box. The variable named by varname (or the
            # second arg) is automatically populated with the box value so that
            # code inside the block can read it as a normal Nova variable.
            # ask() inside the block is rewritten to call get_textbox(id).
            m = re.match(r'^txtbox\s*\(\s*"([^"]+)"\s*(?:,\s*"([^"]*)"\s*)?(?:,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*)?\)$', s)
            if m:
                emitter.has_novaui = True
                lbl     = m.group(1)
                varname = m.group(2) if m.group(2) else None
                tx, ty, tw = m.group(3), m.group(4), m.group(5)
                has_coords = tx is not None
                tb_id_field = emitter.ulabel("_tbid").replace("_tbid", "_tb_id")
                if tb_id_field not in emitter.fields:
                    emitter.fields[tb_id_field] = "int"
                    emitter.cctor += ['ldc.i4.0', f'stsfld int32 {A}::{tb_id_field}']
                if varname and varname not in emitter.fields:
                    emitter.fields[varname] = "string"
                    emitter.cctor += ['ldstr ""', f'stsfld string {A}::{varname}']
                inner, idx = compile_block(idx+1, indent+1)
                prefix = []
                if varname:
                    prefix += [f'ldsfld int32 {A}::{tb_id_field}',
                               f'call string [NovaUI]NovaUI::get_textbox(int32)',
                               f'stsfld string {A}::{varname}']
                hname = "TB_" + str(handler_counter[0]); handler_counter[0] += 1
                emitter.add_handler(hname, prefix + inner)
                if has_coords:
                    il += [f'ldstr "{escape_il(lbl)}"',
                           f'ldc.i4 {tx}', f'ldc.i4 {ty}', f'ldc.i4 {tw}',
                           'ldnull',
                           f'ldftn void {A}::{hname}()',
                           'newobj instance void [mscorlib]System.Action::.ctor(object, native int)',
                           f'call int32 [NovaUI]NovaUI::textbox(string, int32, int32, int32, class [mscorlib]System.Action)',
                           f'stsfld int32 {A}::{tb_id_field}']
                else:
                    il += [f'ldstr "{escape_il(lbl)}"',
                           'ldnull',
                           f'ldftn void {A}::{hname}()',
                           'newobj instance void [mscorlib]System.Action::.ctor(object, native int)',
                           f'call int32 [NovaUI]NovaUI::textbox(string, class [mscorlib]System.Action)',
                           f'stsfld int32 {A}::{tb_id_field}']
                continue

            # ask(prompt) as a statement: result = ask("...")
            # When used as NAME = ask("prompt") it is handled by generic assignment below.
            # Standalone ask() just discards the result.
            m = re.match(r'^ask\s*\(\s*(.*)\s*\)$', s)
            if m:
                prompt_expr = m.group(1).strip()
                if prompt_expr:
                    t = parse(prompt_expr, il); coerce_to_string(t, il)
                else:
                    il.append('ldstr ""')
                il.append(f'call string {A}::_ConsoleAsk(string)')
                il.append('pop')   # discard result
                idx += 1; continue

            # mem_write("name", expr) as statement
            m = re.match(r'^mem_write\s*\(\s*"([^"]+)"\s*,\s*(.+)\s*\)$', s)
            if m:
                seg, expr = m.group(1), m.group(2).strip()
                il.append(f'ldstr "{escape_il(seg)}"')
                t = parse(expr, il); coerce_to_string(t, il)
                il.append(f'call void {A}::_MemWrite(string, string)')
                idx += 1; continue

            # clicked / otherwise (block openers)
            if s.startswith('clicked'):
                inner, idx = compile_block(idx+1, indent+1)
                il += inner; continue
            if s in ('otherwise', 'else'):
                break

            # when
            m = re.match(r'^when\s*\((.+)\)$', s)
            if m:
                end_l  = emitter.ulabel("ENDIF")
                else_l = emitter.ulabel("ELSE")
                parse(m.group(1).strip(), il)
                il.append(f'brfalse {else_l}')
                body, idx = compile_block(idx+1, indent+1)
                il += body + [f'br {end_l}', f'{else_l}:']
                while idx < len(lines):
                    ws  = lines[idx].strip().rstrip('{').rstrip()
                    mww = re.match(r'^(?:whenwise|ww)\s*\((.+)\)$', ws)
                    if not mww: break
                    idx += 1
                    else_l2 = emitter.ulabel("ELSE")
                    parse(mww.group(1).strip(), il)
                    il.append(f'brfalse {else_l2}')
                    wbody, idx = compile_block(idx, indent+1)
                    il += wbody + [f'br {end_l}', f'{else_l2}:']
                if idx < len(lines) and lines[idx].strip().rstrip('{').rstrip() in ('otherwise', 'else'):
                    ob, idx = compile_block(idx+1, indent+1)
                    il += ob
                il.append(f'{end_l}:'); continue

            # while
            m = re.match(r'^while\s*\((.+)\)$', s)
            if m:
                lp = emitter.ulabel("LP"); le = emitter.ulabel("LPEND")
                il.append(f'{lp}:')
                parse(m.group(1).strip(), il)
                il.append(f'brfalse {le}')
                body, idx = compile_block(idx+1, indent+1)
                il += body + [f'br {lp}', f'{le}:']; continue

            # repeat N
            m = re.match(r'^repeat\s+(.+)$', s)
            if m:
                ctr = emitter.ulabel("RC").replace("RC_", "_rc")
                lp  = emitter.ulabel("RPL"); le = emitter.ulabel("RPE")
                if ctr not in emitter.fields:
                    emitter.fields[ctr] = "int"
                    emitter.cctor += ['ldc.i4.0', f'stsfld int32 {A}::{ctr}']
                parse(m.group(1).strip(), il)
                il.append(f'stsfld int32 {A}::{ctr}')
                il.append(f'{lp}:')
                il += [f'ldsfld int32 {A}::{ctr}', f'brfalse {le}',
                       f'ldsfld int32 {A}::{ctr}', 'ldc.i4.1', 'sub',
                       f'stsfld int32 {A}::{ctr}']
                body, idx = compile_block(idx+1, indent+1)
                il += body + [f'br {lp}', f'{le}:']; continue

            # break
            if s == 'break':
                idx += 1; continue

            # put(expr)
            m = re.match(r'^put\s*\(\s*(.+)\s*\)$', s)
            if m:
                arg = m.group(1).strip()
                if arg in emitter.fields and emitter.fields[arg] == "string[]":
                    il += ['ldstr ", "', f'ldsfld class [mscorlib]System.String[] {A}::{arg}',
                           'call string [mscorlib]System.String::Join(string, string[])']
                else:
                    t = parse(arg, il); coerce_to_string(t, il)
                il.append('call void [mscorlib]System.Console::WriteLine(string)')
                idx += 1; continue

            # ui_message(expr)
            m = re.match(r'^ui_message\s*\(\s*(.+)\s*\)$', s)
            if m:
                t = parse(m.group(1).strip(), il); coerce_to_string(t, il)
                il.append(f'call void {A}::_ShowMessage(string)')
                idx += 1; continue

            # icon
            m = re.match(r'^icon\s*\(\s*"([^"]+)"\s*\)$', s)
            if m:
                il += [f'ldstr "{escape_il(m.group(1))}"',
                       f'call void {A}::_SetIcon(string)']
                idx += 1; continue

            # write_file / read_file
            m = re.match(r'^write_file\s*\(\s*"([^"]+)"\s*,\s*(.+)\s*\)$', s)
            if m:
                il.append(f'ldstr "{escape_il(m.group(1))}"')
                t = parse(m.group(2).strip(), il); coerce_to_string(t, il)
                il.append('call void [mscorlib]System.IO.File::WriteAllText(string, string)')
                idx += 1; continue

            m = re.match(r'^(\w+)\s*=\s*read_file\s*\(\s*"([^"]+)"\s*\)$', s)
            if m:
                name, path = m.groups(); read_file_paths.add(path)
                if name not in emitter.fields: emitter.fields[name] = "string"
                il += [f'ldstr "{escape_il(path)}"',
                       'call string [mscorlib]System.IO.File::ReadAllText(string)',
                       f'stsfld string {A}::{name}']
                idx += 1; continue

            # App.Exit / pause
            if s in ('App.Exit', 'App.Exit()', 'ExitApp', 'ExitApp()'):
                il.append(f'call void {A}::_ExitApp()')
                idx += 1; continue
            if s in ('pause', 'pause()'):
                il += ['call valuetype [mscorlib]System.ConsoleKeyInfo [mscorlib]System.Console::ReadKey()', 'pop']
                idx += 1; continue

            # array element assignment  NAME[expr] = expr
            m = re.match(r'^([A-Za-z_]\w*)\s*\[(.+?)\]\s*=\s*(.+)$', s)
            if m:
                aname, idx_expr, val_expr = m.group(1), m.group(2), m.group(3).strip()
                if aname not in emitter.fields: emitter.fields[aname] = "string[]"
                il.append(f'ldsfld class [mscorlib]System.String[] {A}::{aname}')
                it = parse(idx_expr.strip(), il)
                if it == "float": il.append('conv.i4')
                vt = parse(val_expr, il); coerce_to_string(vt, il)
                il.append('stelem.ref')
                idx += 1; continue

            # generic assignment  var = expr
            m = re.match(r'^(\w+)\s*=\s*(.+)$', s)
            if m:
                name, expr = m.group(1), m.group(2).strip()
                if re.match(r'^-?\d+\.\d+$', expr):
                    if name not in emitter.fields:
                        emitter.fields[name] = "float"
                        emitter.cctor += ['ldc.r8 0.0', f'stsfld float64 {A}::{name}']
                    il += [f'ldc.r8 {expr}', f'stsfld float64 {A}::{name}']
                elif expr.lstrip('-').isdigit():
                    ensure_int(name)
                    il += [f'ldc.i4 {expr}', f'stsfld int32 {A}::{name}']
                else:
                    t = parse(expr, il)
                    if name not in emitter.fields:
                        emitter.fields[name] = t
                        if   t == "int":   emitter.cctor += ['ldc.i4.0',   f'stsfld int32   {A}::{name}']
                        elif t == "float": emitter.cctor += ['ldc.r8 0.0', f'stsfld float64 {A}::{name}']
                        else:              emitter.cctor += ['ldstr ""',    f'stsfld string  {A}::{name}']
                    elif emitter.fields[name] != t:
                        # Type mismatch: field was pre-declared as a different type.
                        # Coerce the expression result to match the declared field type.
                        declared = emitter.fields[name]
                        if declared == "string":
                            # Convert int/float result to string before storing
                            if t == "int":
                                il += ["box [mscorlib]System.Int32",
                                       "callvirt instance string [mscorlib]System.Object::ToString()"]
                            elif t == "float":
                                il += ["box [mscorlib]System.Double",
                                       "callvirt instance string [mscorlib]System.Object::ToString()"]
                            t = "string"
                        elif declared == "int" and t == "float":
                            il.append("conv.i4")
                            t = "int"
                        elif declared == "float" and t == "int":
                            il.append("conv.r8")
                            t = "float"
                    store(name, t, il)
                idx += 1; continue

            idx += 1   # unrecognised - skip

        return il, idx

    main_il, _ = compile_block(0, 0)
    emitter._main_lines = main_il
    return emitter, read_file_paths


# --- compiler GUI -------------------------------------------------------------

class NovaCompilerApp:
    def __init__(self, root):
        self.root = root
        root.title("Nova Compiler"); root.geometry("900x720")
        self.script_dir   = os.path.dirname(os.path.abspath(__file__))
        self.src_dir      = tk.StringVar(value=os.getcwd())
        self.out_name     = tk.StringVar(value="NovaProgram")
        self.ilasm_path   = tk.StringVar(value=find_ilasm_path() or "")
        self.compile_type = tk.StringVar(value="exe")
        self.last_il      = ""
        self.BG = "#1e1e1e"; self.FG = "#ffffff"
        self.BTN = "#333333"; self.EBG = "#2d2d2d"
        root.configure(bg=self.BG)
        self._build_ui(); self._refresh()

    def _build_ui(self):
        top = tk.Frame(self.root, padx=6, pady=6, bg=self.BG); top.pack(fill="x")
        tk.Label(top, text="Source Folder:", bg=self.BG, fg=self.FG).pack(side="left")
        tk.Entry(top, textvariable=self.src_dir, width=60, bg=self.EBG, fg=self.FG,
                 insertbackground="white").pack(side="left", padx=5)
        tk.Button(top, text="Browse", command=self._browse, bg=self.BTN, fg=self.FG).pack(side="left")

        mid  = tk.Frame(self.root, padx=8, bg=self.BG); mid.pack(fill="both", expand=True)
        left = tk.Frame(mid, bg=self.BG); left.pack(side="left", fill="both", expand=True)
        tk.Label(left, text=".nova files:", bg=self.BG, fg=self.FG).pack(anchor="w")
        self.listbox = tk.Listbox(left, bg=self.EBG, fg=self.FG, selectbackground="#444")
        self.listbox.pack(fill="both", expand=True)
        tk.Button(left, text="Refresh", command=self._refresh, bg=self.BTN, fg=self.FG).pack(fill="x", pady=6)

        right = tk.Frame(mid, width=320, padx=8, bg=self.BG); right.pack(side="right", fill="y")
        tk.Label(right, text="Compile Settings", font=("Arial", 10, "bold"), bg=self.BG, fg=self.FG).pack(anchor="w")
        for txt, val in [("Standalone App (.exe)", "exe"), ("Library (.dll)", "dll")]:
            tk.Radiobutton(right, text=txt, variable=self.compile_type, value=val,
                           bg=self.BG, fg=self.FG, selectcolor=self.BG).pack(anchor="w")
        for lbl, var in [("\nOutput Base Name:", self.out_name), ("\nilasm path (optional):", self.ilasm_path)]:
            tk.Label(right, text=lbl, bg=self.BG, fg=self.FG).pack(anchor="w")
            tk.Entry(right, textvariable=var, bg=self.EBG, fg=self.FG,
                     insertbackground="white").pack(fill="x", pady=5)
        tk.Button(right, text="Compile .nova Files", command=self._compile,
                  height=2, bg="#0ba300", fg="white").pack(fill="x", pady=10)
        tk.Button(right, text="Open Output Folder", command=self._open_out,
                  bg=self.BTN, fg=self.FG).pack(fill="x", pady=2)
        tk.Button(right, text="Show Generated IL", command=self._show_il,
                  bg=self.BTN, fg=self.FG).pack(fill="x", pady=2)
        self.log = scrolledtext.ScrolledText(self.root, height=18, bg="black", fg="#00ff00")
        self.log.pack(fill="both", padx=8, pady=8)

    def _browse(self):
        d = filedialog.askdirectory(initialdir=self.src_dir.get())
        if d: self.src_dir.set(d); self._refresh()

    def _refresh(self):
        self.listbox.delete(0, tk.END)
        try:
            for f in os.listdir(self.src_dir.get()):
                if f.lower().endswith(".nova"): self.listbox.insert(tk.END, f)
        except Exception as e: self.log.insert(tk.END, f"Error: {e}\n")

    def _show_il(self):
        if not self.last_il: return
        top = tk.Toplevel(self.root); top.title("Generated IL"); top.configure(bg=self.BG)
        txt = scrolledtext.ScrolledText(top, bg=self.BG, fg=self.FG); txt.pack(fill="both", expand=True)
        txt.insert("1.0", self.last_il)

    def _open_out(self):
        try: os.startfile(self.src_dir.get())
        except Exception as e: self.log.insert(tk.END, f"Error: {e}\n")

    def _compile(self):
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showwarning("No file selected", "Please select a .nova file."); return
        fname = self.listbox.get(sel[0])
        with open(os.path.join(self.src_dir.get(), fname), encoding="utf-8") as f: src = f.read()
        out_base = self.out_name.get().strip() or "NovaProgram"
        emitter, read_paths = translate_nova_to_il(src, out_base)
        self.last_il = emitter.get_il(emitter._main_lines)
        out_folder = os.path.abspath(self.src_dir.get())
        il_path = os.path.join(out_folder, out_base + ".il")
        with open(il_path, "w", encoding="utf-8") as f: f.write(self.last_il)
        for p in read_paths:
            tp = os.path.join(out_folder, p)
            if not os.path.exists(tp):
                if os.path.dirname(p): os.makedirs(os.path.join(out_folder, os.path.dirname(p)), exist_ok=True)
                open(tp, "w").close()
        if emitter.has_novaui:
            candidates = []
            for name in ("NovaUI.dll", "novaui.dll"):
                for folder in (out_folder, self.script_dir, os.getcwd()):
                    candidates.append(os.path.join(folder, name))
            dll_src = next((p for p in candidates if os.path.isfile(p)), None)
            dst = os.path.join(out_folder, "NovaUI.dll")
            if dll_src and os.path.abspath(dll_src) != os.path.abspath(dst):
                try: shutil.copy2(dll_src, dst); self.log.insert(tk.END, "Copied NovaUI.dll\n")
                except Exception as e: self.log.insert(tk.END, "Warning: " + str(e) + "\n")
            elif not dll_src:
                self.log.insert(tk.END, "Warning: NovaUI.dll not found.\n")
        ilasm = self.ilasm_path.get() or find_ilasm_path()
        if not ilasm: self.log.insert(tk.END, "Error: ilasm.exe not found.\n"); return
        ext      = ".exe" if self.compile_type.get() == "exe" else ".dll"
        out_file = os.path.join(out_folder, out_base + ext)
        cmd      = [ilasm, il_path, f"/{self.compile_type.get()}", f"/output={out_file}"]
        self.log.insert(tk.END, f"Running: {' '.join(cmd)}\n")
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, cwd=out_folder)
            self.log.insert(tk.END, p.stdout + p.stderr + "\n")
        except Exception as ex: self.log.insert(tk.END, f"Error: {ex}\n")
        self.log.insert(tk.END, "Done.\n"); self.log.see(tk.END)


if __name__ == "__main__":
    root = tk.Tk()
    NovaCompilerApp(root)
    root.mainloop()
