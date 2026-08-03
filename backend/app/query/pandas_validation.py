"""Part 2, pandas path: an AST ALLOWLIST, never a denylist. A denylist of
dangerous names ('reject os, sys, subprocess...') is a check that can be
bypassed by a name nobody thought to list; an allowlist of permitted node
types and names cannot be bypassed the same way, because anything not
explicitly permitted is rejected by construction - including every future
dangerous name nobody has thought of yet.

Generated code must be a short sequence of simple statements (assignment
and bare expressions only - no import, no control flow, no function/class
definitions) operating on a single variable named `df`, and must assign its
final answer to a variable named `result`. A validation failure is NEVER
repaired by re-prompting the model with the error (Part 2) - the caller
escalates instead.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

DATAFRAME_NAME = "df"
RESULT_NAME = "result"

# Deliberately small and pure - no I/O, no introspection, nothing that can
# reach outside the expression it's called in. Absent from this set means
# absent from the executable namespace (app/query/sandbox.py builds the
# exec globals from exactly this allowlist) - "eval", "exec", "open",
# "__import__", "getattr", "compile", "globals", "locals", "vars" are all
# simply not here, and there is no separate rule that has to remember to
# reject them.
ALLOWED_BUILTIN_NAMES = frozenset(
    {
        "len", "sum", "min", "max", "round", "abs", "sorted", "list", "dict",
        "tuple", "set", "str", "int", "float", "bool", "range", "enumerate",
        "zip", "reversed", "any", "all",
    }
)

# Every AST node type a generated pandas one-liner (or short sequence of
# assignments) can legitimately need. No control flow (If/For/While),
# no definitions (FunctionDef/ClassDef/Lambda-with-closures - Lambda itself
# IS allowed, see visit_Lambda), no import, nothing this list doesn't name.
_ALLOWED_NODE_TYPES: tuple[type[ast.AST], ...] = (
    ast.Module,
    ast.Expr,
    ast.Assign,
    ast.AugAssign,
    ast.Name,
    ast.Attribute,
    ast.Subscript,
    ast.Slice,
    ast.Call,
    ast.keyword,
    ast.Starred,
    ast.Constant,
    ast.List,
    ast.Tuple,
    ast.Dict,
    ast.Set,
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    ast.Lambda,
    ast.arguments,
    ast.arg,
    ast.Load,
    ast.Store,
    ast.Del,
    # operators - BitAnd/BitOr/BitXor/Invert included deliberately: `&`/`|`/`~`
    # are how pandas boolean masks are actually combined (df[(a > 1) & (b < 2)]),
    # NOT `and`/`or`/`not`, which don't work element-wise on a Series.
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd, ast.Not, ast.Invert,
    ast.And, ast.Or,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Is, ast.IsNot, ast.In, ast.NotIn,
    ast.BitAnd, ast.BitOr, ast.BitXor,
)


@dataclass
class PandasValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)


class _AllowlistValidator(ast.NodeVisitor):
    def __init__(self) -> None:
        self.errors: list[str] = []
        # A stack of locally-bound name sets (module scope at index 0, one
        # more pushed per Lambda entered) - a Name is permitted in Load
        # context if it's bound in ANY active scope, is the dataframe name,
        # or is on the fixed builtins allowlist. A lambda parameter is
        # bound only within its own scope, so a body referencing anything
        # else automatically fails this check - that IS the "no closures
        # over anything but the frame" rule, with no separate case for it.
        self._scopes: list[set[str]] = [set()]

    def visit(self, node: ast.AST):
        if not isinstance(node, _ALLOWED_NODE_TYPES):
            self.errors.append(f"disallowed syntax: {type(node).__name__}")
            return None  # do not descend into a rejected node
        return super().visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            bound = {name for scope in self._scopes for name in scope}
            if node.id != DATAFRAME_NAME and node.id not in ALLOWED_BUILTIN_NAMES and node.id not in bound:
                self.errors.append(f"name '{node.id}' is not allowed")
        else:  # Store, Del
            self._scopes[-1].add(node.id)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("__") and node.attr.endswith("__"):
            self.errors.append(f"dunder attribute access is not allowed: '.{node.attr}'")
        self.generic_visit(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        params = {a.arg for a in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)}
        if node.args.vararg:
            params.add(node.args.vararg.arg)
        if node.args.kwarg:
            params.add(node.args.kwarg.arg)
        self._scopes.append(params)
        self.generic_visit(node)
        self._scopes.pop()

    @property
    def result_assigned(self) -> bool:
        return RESULT_NAME in self._scopes[0]


def validate_pandas_code(code: str) -> PandasValidationResult:
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        return PandasValidationResult(valid=False, errors=[f"could not parse code: {exc}"])

    validator = _AllowlistValidator()
    validator.visit(tree)

    if not validator.errors and not validator.result_assigned:
        validator.errors.append(f"code must assign its final answer to a variable named '{RESULT_NAME}'")

    return PandasValidationResult(valid=not validator.errors, errors=validator.errors)
