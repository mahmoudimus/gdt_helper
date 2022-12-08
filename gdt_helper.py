import os
import re
import subprocess
from typing import List
from clang_parser.CParser import CParser
from clang_parser.CLexer import CLexer
from clang_parser.CListener import CListener
from antlr4 import CommonTokenStream, ParseTreeWalker, InputStream

import typer

app = typer.Typer()


def show_err(msg: str) -> None:
    typer.secho(msg, err=True, fg=typer.colors.RED)


def show_warn(msg: str) -> None:
    typer.secho(msg, err=True, fg=typer.colors.YELLOW)


def show_log(msg: str) -> None:
    typer.secho(msg, err=True, fg=typer.colors.GREEN)


def has_compiler(compiler_path: str) -> bool:
    try:
        subprocess.run(
            [compiler_path, "-v"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        ).stdout.decode("utf-8")
    except FileNotFoundError:
        show_err(f"{compiler_path} is not installed")
        show_err("Please install via package manager")
        return False
    except Exception as e:
        show_err(f"Unknown error ({e})")
        return False
    return True


def get_default_include_paths(compiler_raw_output: str) -> List[str]:
    include_path_str_starts = False
    default_include_paths = list()
    for line in compiler_raw_output.split("\n"):
        if line.startswith("#include <...> search starts here:"):
            include_path_str_starts = True
            continue
        if line.startswith("End of search list."):
            break
        if include_path_str_starts:
            default_include_paths.append("-I" + line.strip())
    return default_include_paths


def get_compiler_paths(compiler_raw_output: str) -> List[str]:
    for line in compiler_raw_output.split("\n"):
        if line.startswith("COMPILER_PATH="):
            path_var = line.split("=")[-1]
            return ["-I" + i for i in path_var.split(":")]
    return []


def get_additional_definitions() -> List[str]:
    return [
        '-DCONST="const"',
        '-D__restrict__=""',
        '-D__always_inline__="inline"',
        '-D__gnu_inline__="inline"',
        '-D__builtin_va_list="void *"',
    ]


def get_default_definitions(compiler_path: str) -> List[str]:
    compiler_raw_output = subprocess.run(
        [compiler_path, "-std=c89", "-dM", "-E", "-"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    ).stdout.decode("utf-8")
    defs = list()
    for line in compiler_raw_output.split("\n"):
        if not line:
            continue
        define, *vars = line.split(" ")
        if define != "#define":
            show_err("It is not #define")
            show_err(line)
            show_err("It might fail to parse")
        defs.append("-D" + vars[0] + "=" + '"' + " ".join(vars[1:]) + '"')
    return defs


def get_parse_options(compiler_path: str) -> List[str]:
    compiler_raw_output = subprocess.run(
        [compiler_path, "-std=c89", "-E", "-v", "-xc", "-"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    ).stdout.decode("utf-8")
    default_include_paths = get_default_include_paths(compiler_raw_output)
    compiler_paths = get_compiler_paths(compiler_raw_output)
    defs = get_additional_definitions()
    default_defs = get_default_definitions(compiler_path)
    return default_include_paths + compiler_paths + defs + default_defs


@app.command()
def make_parse_options(compiler_path: str) -> None:
    if not has_compiler(compiler_path):
        return

    try:
        parse_options = get_parse_options(compiler_path)
        show_warn('Please past the following output to "Parse Options"\n')
        typer.echo("\n".join(parse_options))
    except Exception as e:
        show_err(str(e))
        show_err("Cannot make parse options")


class GhidraValidTokensExtractor(CListener):
    def __init__(self) -> None:
        self.code_out = ""

    def enterFunctionDefinition(self, ctx: CParser.FunctionDefinitionContext) -> None:
        input = ctx.start.getInputStream()
        compound_stmt: CParser.CompoundStatementContext = ctx.getTypedRuleContext(CParser.CompoundStatementContext, 0)
        self.code_out += input.getText(ctx.start.start, compound_stmt.start.start-1) + ";\n"

    def enterDeclaration(self, ctx: CParser.DeclarationContext) -> None:
        input = ctx.start.getInputStream()
        self.code_out += input.getText(ctx.start.start, ctx.stop.stop) + "\n"


def extract_valid_tokens(raw_in: str) -> str:
    parser = CParser(CommonTokenStream(CLexer(InputStream(raw_in))))
    walker = ParseTreeWalker()
    listener = GhidraValidTokensExtractor()
    walker.walk(listener, parser.compilationUnit())
    return listener.code_out


@app.command()
def make_file_to_parse(
    compiler_path: str,
    input_header_path: str,
    additional_includes: List[str] = typer.Option([]),
) -> None:
    if not os.path.exists(input_header_path):
        show_err(f"{input_header_path} does not exist")
        return
    if not has_compiler(compiler_path):
        return

    additional_includes = ["-I" + inc for inc in additional_includes]
    raw_out = None
    try:
        compile_cmds = [
            compiler_path,
            "-std=c89",
            "-P",
            "-E",
            input_header_path,
        ] + additional_includes
        raw_out = subprocess.run(
            compile_cmds,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
    except Exception as e:
        show_err(str(e))
        if raw_out is not None:
            show_err(raw_out.stderr.decode("utf-8"))
            show_err(raw_out.stdout.decode("utf-8"))
        return

    source_to_parse = raw_out.stdout.decode("utf-8")
    source_to_parse = extract_valid_tokens(source_to_parse)
    if "x86_64-w64-mingw32-gcc" in compiler_path:
        show_warn("Post processing for mingw64")
        show_warn("You might encounter Ghidra parse errors for this file.")
        show_warn(
            "If so, please remove some functions (_cvtsh_ss in my envorinment)"
            " manually according to CParserPlugin.out\n"
        )
    output_header_path = input_header_path + ".out"
    with open(output_header_path, "w") as fout:
        fout.write(source_to_parse)

    show_warn(f'Please add "{output_header_path}" to "Source files to parse"\n')
    if additional_includes:
        show_warn("You also have specified additional includes as arguments")
        show_warn(
            'So, please add the following header includes to "Parse configuration"'
        )
        typer.echo("\n".join(additional_includes))


if __name__ == "__main__":
    app()
