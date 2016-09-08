# -*- coding: utf-8 -*-

import base64
import contextlib
from io import StringIO
import importlib
import os
import py_compile
import re
import shutil
import subprocess
import sys
import traceback
from unittest import TestCase


# A state variable to determine if the test environment has been configured.
_batavia_built = False
_phantomjs = None


def build_batavia():
    """Build the Batavia library

    This only needs to be run once, prior to the first test.
    """
    global _batavia_built
    if _batavia_built:
        return

    proc = subprocess.Popen(
        "make",
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        shell=True,
    )

    try:
        out, err = proc.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        raise

    if proc.returncode != 0:
        raise Exception("Error compiling batavia sources: " + out.decode('ascii'))

    _batavia_built = True


@contextlib.contextmanager
def capture_output(redirect_stderr=True):
    oldout, olderr = sys.stdout, sys.stderr
    try:
        out = StringIO()
        sys.stdout = out
        if redirect_stderr:
            sys.stderr = out
        else:
            sys.stderr = StringIO()
        yield out
    except:
        if redirect_stderr:
            traceback.print_exc()
        else:
            raise
    finally:
        sys.stdout, sys.stderr = oldout, olderr


def adjust(text, run_in_function=False):
    """Adjust a code sample to remove leading whitespace."""
    lines = text.split('\n')
    if len(lines) == 1:
        return text

    if lines[0].strip() == '':
        lines = lines[1:]
    first_line = lines[0].lstrip()
    n_spaces = len(lines[0]) - len(first_line)

    final_lines = [('    ' if run_in_function else '') + line[n_spaces:] for line in lines]

    if run_in_function:
        final_lines = [
            "def test_function():",
        ] + final_lines + [
            "test_function()",
        ]

    return '\n'.join(final_lines)


def runAsPython(test_dir, main_code, extra_code=None, run_in_function=False, args=None):
    """Run a block of Python code with the Python interpreter."""
    # Output source code into test directory
    with open(os.path.join(test_dir, 'test.py'), 'w', encoding='utf-8') as py_source:
        py_source.write(adjust(main_code, run_in_function=run_in_function))

    if extra_code:
        for name, code in extra_code.items():
            path = name.split('.')
            path[-1] = path[-1] + '.py'
            if len(path) != 1:
                try:
                    os.makedirs(os.path.join(test_dir, *path[:-1]))
                except FileExistsError:
                    pass
            with open(os.path.join(test_dir, *path), 'w') as py_source:
                py_source.write(adjust(code))

    if args is None:
        args = []

    env_copy = os.environ.copy()
    env_copy['PYTHONIOENCODING'] = 'UTF-8'
    proc = subprocess.Popen(
        [sys.executable, "test.py"] + args,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=test_dir,
        env=env_copy,
    )
    out = proc.communicate()

    return out[0].decode('utf8')


class PhantomJSCrash(RuntimeError):
    pass


def sendPhantomCommand(phantomjs, payload=None, output=None, success=None, on_fail=None):
    if payload:
        cmd = adjust(payload).replace('\n', '')
        # print("<<<", cmd)

        _phantomjs.stdin.write(cmd.encode('utf-8'))
        _phantomjs.stdin.write('\n'.encode('utf-8'))
        _phantomjs.stdin.flush()

    # print("WAIT FOR PROMPT...")
    if output is not None:
        out = output
    else:
        out = []

    out.append(b'')
    while out[-1] != b"phantomjs> " and out[-1] != b'PhantomJS has crashed. ':
        try:
            ch = _phantomjs.stdout.read(1)
            if ch == b'\n':
                # print(">>>", out[-1])
                out[-1] = out[-1].decode("utf-8")
                out.append(b'')
            elif ch != b'\r':
                out[-1] += ch
        except IOError:
            continue

    if out[-1] == 'PhantomJS has crashed. ':
        raise PhantomJSCrash()

    if payload:
        # Drop the prompt line
        out.pop()
        # Get the response line
        response = out.pop()
        # print("COMMAND EXECUTED: ", response)
        if success:
            if isinstance(success, (list, tuple)):
                if response not in success:
                    if on_fail:
                        raise Exception(on_fail + ": %s" % response)
                    else:
                        raise Exception("Didn't receive an expected response: %s" % response)
            else:
                if response != success:
                    if on_fail:
                        raise Exception(on_fail + ": %s" % response)
                    else:
                        raise Exception("Didn't receive the expected response: %s" % response)

        # Drop a trailing blank line, if one exists.
        if len(out) > 1 and out[-1] == '':
            out.pop()

        return '\n'.join(out).replace('\n\n', '\n') + '\n'
    else:
        # print("PHANTOMJS READY")
        return None


def runAsJavaScript(test_dir, main_code, extra_code=None, js=None, run_in_function=False, args=None):
    # Output source code into test directory
    assert isinstance(main_code, (str, bytes)), (
        'I have no idea how to run tests for code of type {}'
        ''.format(type(main_code))
    )

    if isinstance(main_code, str):
        py_filename = os.path.join(test_dir, 'test.py')
        with open(py_filename, 'w', encoding='utf-8') as py_source:
            py_source.write(adjust(main_code, run_in_function=run_in_function))

    modules = {}

    # Temporarily move into the test directory.
    cwd = os.getcwd()
    os.chdir(test_dir)

    if isinstance(main_code, str):
        py_compile.compile('test.py')
        with open(importlib.util.cache_from_source('test.py'), 'rb') as compiled:
            modules['testcase'] = base64.encodebytes(compiled.read())
    elif isinstance(main_code, bytes):
        modules['testcase'] = main_code

    if extra_code:
        for name, code in extra_code.items():
            path = name.split('.')
            path[-1] = path[-1] + '.py'
            if len(path) != 1:
                try:
                    os.makedirs(os.path.join(test_dir, *path[:-1]))
                except FileExistsError:
                    pass

            py_filename = os.path.join(*path)
            with open(py_filename, 'w') as py_source:
                py_source.write(adjust(code))

            py_compile.compile(py_filename)
            with open(importlib.util.cache_from_source(py_filename), 'rb') as compiled:
                modules[name] = base64.encodebytes(compiled.read())

    # Move back to the old current working directory.
    os.chdir(cwd)

    if args is None:
        args = []

    # Convert the dictionary of modules into a payload
    payload = []
    for name, code in modules.items():
        lines = code.decode('utf-8').split('\n')
        output = '"%s"' % '" +\n        "'.join(line for line in lines if line)
        if name.endswith('.__init__'):
            name = name.rsplit('.', 1)[0]
        payload.append('    "%s": %s' % (name, output))

    with open(os.path.join(test_dir, 'modules.js'), 'w') as js_file:
        js_file.write(adjust("""
            var modules = {
            %s
            };
            """) % (
                ',\n'.join(payload)
            )
        )

    global _phantomjs
    out = None
    while out is None:
        try:
            if _phantomjs is None:
                build_batavia()

                if _phantomjs is None:
                    # Make sure Batavia is compiled

                    # Start the phantomjs environment.
                    _phantomjs = subprocess.Popen(
                        ["phantomjs"],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        cwd=os.path.dirname(__file__),
                    )
                    sendPhantomCommand(_phantomjs)

            sendPhantomCommand(
                _phantomjs,
                "var page = require('webpage').create()",
                success='undefined',
                on_fail="Unable to create webpage."
            )

            sendPhantomCommand(
                _phantomjs,
                """
                page.onConsoleMessage = function (msg) {
                    console.log(msg);
                }
                """,
                success=['undefined', '{}'],
                on_fail="Unable to create console redirection"
            )

            sendPhantomCommand(
                _phantomjs,
                "page.injectJs('polyfill.js')",
                success=['true', '{}'],
                on_fail="Unable to inject polyfill"
            )
            sendPhantomCommand(
                _phantomjs,
                "page.injectJs('../batavia.min.js')",
                success=['true', '{}'],
                on_fail="Unable to inject Batavia"
            )
            sendPhantomCommand(
                _phantomjs,
                "page.injectJs('%s')" % 'temp/modules.js',
                success=['true', '{}'],
                on_fail="Unable to inject modules"
            )

            output = []
            if js is not None:
                for mod, payload in sorted(js.items()):
                    sendPhantomCommand(
                        _phantomjs,
                        "page.injectJs('%s.js')" % "/".join(('temp', mod)),
                        output=output,
                        success=['true', '{}'],
                        on_fail="Unable to inject native module %s" % mod
                    )

            out = sendPhantomCommand(
                _phantomjs,
                """
                page.evaluate(function() {
                    var vm = new batavia.VirtualMachine(function(name) {
                        return modules[name];
                    });
                    vm.run('testcase', []);
                });
                """,
                output=output
            )
        except PhantomJSCrash:
            _phantomjs.kill()
            _phantomjs.stdin.close()
            _phantomjs.stdout.close()
            _phantomjs = None

    return out


JS_EXCEPTION = re.compile('Traceback \(most recent call last\):\r?\n(  File "(?P<file>.*)", line (?P<line>\d+), in .*\r?\n)+(?P<exception>.*?): (?P<message>.*\r?\n)')
JS_STACK = re.compile('  File "(?P<file>.*)", line (?P<line>\d+), in .*\r?\n')
JS_BOOL_TRUE = re.compile('true')
JS_BOOL_FALSE = re.compile('false')
JS_FLOAT = re.compile('(\d+)e(-)?0?(\d+)')
JS_FLOAT_ROUND = re.compile('(\\.\d+)0000000000\d')

PYTHON_EXCEPTION = re.compile('Traceback \(most recent call last\):\r?\n(  File "(?P<file>.*)", line (?P<line>\d+), in .*\r?\n    .*\r?\n)+(?P<exception>.*?): (?P<message>.*\r?\n)')
PYTHON_STACK = re.compile('  File "(?P<file>.*)", line (?P<line>\d+), in .*\r?\n    .*\r?\n')
PYTHON_FLOAT = re.compile('(\d+)e(-)?0?(\d+)')
PYTHON_FLOAT_ROUND = re.compile('(\\.\d+)0000000000\d')
PYTHON_NEGATIVE_ZERO_J = re.compile('-0j\)')

MEMORY_REFERENCE = re.compile('0x[\dABCDEFabcdef]{4,16}')


def cleanse_javascript(input, substitutions):
    # Test the specific message
    out = JS_EXCEPTION.sub('### EXCEPTION ###{linesep}\\g<exception>: \\g<message>'.format(linesep=os.linesep), input)

    stack = JS_STACK.findall(input)

    stacklines = []
    test_dir = os.path.join(os.getcwd(), 'tests', 'temp')
    for filename, line in stack:
        if filename.startswith(test_dir):
            filename = filename[len(test_dir)+1:]
        stacklines.append(
            "    %s:%s" % (
                filename, line
            )
        )

    out = '%s%s%s' % (
        out,
        os.linesep.join(stacklines),
        os.linesep if stack else ''
    )
    out = MEMORY_REFERENCE.sub("0xXXXXXXXX", out)
    out = JS_BOOL_TRUE.sub("True", out)
    out = JS_BOOL_FALSE.sub("False", out)
    out = JS_FLOAT.sub('\\1e\\2\\3', out)
    out = JS_FLOAT_ROUND.sub('\\1', out)
    out = out.replace("'test.py'", '***EXECUTABLE***')

    if substitutions:
        for to_value, from_values in substitutions.items():
            for from_value in from_values:
                out = out.replace(from_value, to_value)

    out = out.replace('\r\n', '\n')
    return out


def cleanse_python(input, substitutions):
    # Test the specific message
    out = PYTHON_EXCEPTION.sub('### EXCEPTION ###{linesep}\\g<exception>: \\g<message>'.format(linesep=os.linesep), input)

    stack = PYTHON_STACK.findall(input)
    out = '%s%s%s' % (
        out,
        os.linesep.join(
            [
                "    %s:%s" % (s[0], s[1])
                for s in stack
            ]
        ),
        os.linesep if stack else ''
    )
    out = MEMORY_REFERENCE.sub("0xXXXXXXXX", out)
    out = PYTHON_FLOAT.sub('\\1e\\2\\3', out)
    out = PYTHON_FLOAT_ROUND.sub('\\1', out)
    out = PYTHON_NEGATIVE_ZERO_J.sub('+0j)', out)
    out = out.replace("'test.py'", '***EXECUTABLE***')

    # Python 3.4.4 changed the message describing strings in exceptions
    out = out.replace(
        'argument must be a string or',
        'argument must be a string, a bytes-like object or'
    )

    if substitutions:
        for to_value, from_values in substitutions.items():
            for from_value in from_values:
                out = out.replace(from_value, to_value)

    out = out.replace('\r\n', '\n')
    return out


class TranspileTestCase(TestCase):
    def assertCodeExecution(
            self, code,
            message=None,
            extra_code=None,
            run_in_global=True, run_in_function=True,
            args=None, substitutions=None):
        "Run code as native python, and under JavaScript and check the output is identical"
        self.maxDiff = None
        #==================================================
        # Pass 1 - run the code in the global context
        #==================================================
        if run_in_global:
            try:
                # Create the temp directory into which code will be placed
                test_dir = os.path.join(os.path.dirname(__file__), 'temp')
                try:
                    os.mkdir(test_dir)
                except FileExistsError:
                    pass

                # Run the code as Python and as JavaScript.
                py_out = runAsPython(
                    test_dir,
                    code,
                    extra_code=extra_code,
                    run_in_function=False,
                    args=args
                )
                js_out = runAsJavaScript(
                    test_dir,
                    code,
                    extra_code=extra_code,
                    run_in_function=False,
                    args=args
                )
            except Exception as e:
                self.fail(e)
            finally:
                # Clean up the test directory where the class file was written.
                shutil.rmtree(test_dir)
                # print(js_out)

            # Cleanse the Python and JavaScript output, producing a simple
            # normalized format for exceptions, floats etc.
            js_out = cleanse_javascript(js_out, substitutions)
            py_out = cleanse_python(py_out, substitutions)

            # Confirm that the output of the JavaScript code is the same as the Python code.
            if message:
                context = 'Global context: %s' % message
            else:
                context = 'Global context'
            self.assertEqual(js_out, py_out, context)

        #==================================================
        # Pass 2 - run the code in a function's context
        #==================================================
        if run_in_function:
            try:
                # Create the temp directory into which code will be placed
                test_dir = os.path.join(os.path.dirname(__file__), 'temp')
                try:
                    os.mkdir(test_dir)
                except FileExistsError:
                    pass

                # Run the code as Python and as Java.
                py_out = runAsPython(
                    test_dir,
                    code,
                    extra_code=extra_code,
                    run_in_function=True,
                    args=args
                )
                js_out = runAsJavaScript(
                    test_dir,
                    code,
                    extra_code=extra_code,
                    run_in_function=True,
                    args=args
                )
            except Exception as e:
                self.fail(e)
            finally:
                # Clean up the test directory where the class file was written.
                shutil.rmtree(test_dir)
                # print(js_out)

            # Cleanse the Python and JavaScript output, producing a simple
            # normalized format for exceptions, floats etc.
            js_out = cleanse_javascript(js_out, substitutions)
            py_out = cleanse_python(py_out, substitutions)

            # Confirm that the output of the JavaScript code is the same as the Python code.
            if message:
                context = 'Function context: %s' % message
            else:
                context = 'Function context'
            self.assertEqual(js_out, py_out, context)

    def assertJavaScriptExecution(
            self, code, out,
            extra_code=None, js=None,
            run_in_global=True, run_in_function=True,
            args=None, substitutions=None):
        "Run code under JavaScript and check the output is as expected"
        self.maxDiff = None
        #==================================================
        # Prep - compile any required JavaScript sources
        #==================================================
        # Cleanse the Python output, producing a simple
        # normalized format for exceptions, floats etc.
        py_out = adjust(out)

        #==================================================
        # Pass 1 - run the code in the global context
        #==================================================
        if run_in_global:
            try:
                # Create the temp directory into which code will be placed
                test_dir = os.path.join(os.path.dirname(__file__), 'temp')
                try:
                    os.mkdir(test_dir)
                except FileExistsError:
                    pass

                for mod, payload in js.items():
                    with open(os.path.join(test_dir, '%s.js' % mod), 'w') as jsfile:
                        jsfile.write(adjust(payload))

                # Run the code as Javascript.
                js_out = runAsJavaScript(
                    test_dir,
                    code,
                    extra_code=extra_code,
                    js=js,
                    run_in_function=False,
                    args=args
                )
            except Exception as e:
                self.fail(e)
            finally:
                # Clean up the test directory where the class file was written.
                shutil.rmtree(test_dir)
                # print(js_out)

            # Cleanse the JavaScript output, producing a simple
            # normalized format for exceptions, floats etc.
            js_out = cleanse_javascript(js_out, substitutions)

            # Confirm that the output of the JavaScript code is the same as the Python code.
            self.assertEqual(js_out, py_out, 'Global context')

        #==================================================
        # Pass 2 - run the code in a function's context
        #==================================================
        if run_in_function:
            try:
                # Create the temp directory into which code will be placed
                test_dir = os.path.join(os.path.dirname(__file__), 'temp')
                try:
                    os.mkdir(test_dir)
                except FileExistsError:
                    pass

                for mod, payload in js.items():
                    with open(os.path.join(test_dir, '%s.js' % mod), 'w') as jsfile:
                        jsfile.write(adjust(payload))

                # Run the code as JavaScript.
                js_out = runAsJavaScript(
                    test_dir,
                    code,
                    extra_code=extra_code,
                    js=js,
                    run_in_function=True,
                    args=args
                )
            except Exception as e:
                self.fail(e)
            finally:
                # Clean up the test directory where the class file was written.
                shutil.rmtree(test_dir)
                # print(js_out)

            # Cleanse the JavaScript output, producing a simple
            # normalized format for exceptions, floats etc.
            js_out = cleanse_javascript(js_out, substitutions)

            # Confirm that the output of the JavaScript code is the same as the Python code.
            self.assertEqual(js_out, py_out, 'Function context')


class NotImplementedToExpectedFailure:
    def run(self, result=None):
        # Override the run method to inject the "expectingFailure" marker
        # when the test case runs.
        if self._testMethodName in getattr(self, 'not_implemented', []):
            # Mark 'expecting failure' on class. It will only be applicable
            # for this specific run.
            method = getattr(self, self._testMethodName)
            wrapper = lambda *args, **kwargs: method(*args, **kwargs)
            wrapper.__unittest_expecting_failure__ = True
            setattr(self, self._testMethodName, wrapper)
        return super().run(result=result)


SAMPLE_DATA = {
    'bool': [
            'True',
            'False',
        ],
    'bytearray': [
            #'bytearray()',
            #'bytearray(1)',
            #'bytearray([1, 2, 3])',
            'bytearray(b"hello world")',
        ],
    'bytes': [
            'b""',
            'b"This is another string of bytes"'
        ],
    'class': [
            'type(1)',
            'type("a")',
            #'type(object())', # TODO: re-enable this when object() is implemented
            'type("MyClass", (object,), {})',
        ],
    'complex': [
            '1j',
            '3.14159265j',
            '1+2j',
            '3-4j',
            # '-5j',
        ],
    'dict': [
            '{}',
            '{"a": 1, "c": 2.3456, "d": "another"}',
        ],
    'float': [
            '2.3456',
            '0.0',
            '-3.14159',
            '-4.81756',
            '5.5',
            '-3.5',
            '4.5',
            '-4.5',
        ],
    'frozenset': [
            'frozenset()',
            'frozenset([1])',
            # 'frozenset({"1"})', this reveals some bugs in our code
            'frozenset({1, 2.3456, "another"})',
        ],
    'int': [
            '3',
            '0',
            '-5',
            '-3',
            '5',
            '1',
            '-1',
            '9223372036854775807',
            '9223372036854775808',
            '-9223372036854775807',
            '-9223372036854775808',
            '18446744073709551615',
            '18446744073709551616',
            '18446744073709551617',
            '-18446744073709551615',
            '-18446744073709551616',
            '-18446744073709551617',
            '1361129467683753853853498429727072845824',
            '-1361129467683753853853498429727072845824',
            '179769313486231590772930519078902473361797697894230657273430081157732675805500963132708477322407536021120113879871393357658789768814416622492847430639474124377767893424865485276302219601246094119453082952085005768838150682342462881473913110540827237163350510684586298239947245938479716304835356329624224137216',
            '-179769313486231590772930519078902473361797697894230657273430081157732675805500963132708477322407536021120113879871393357658789768814416622492847430639474124377767893424865485276302219601246094119453082952085005768838150682342462881473913110540827237163350510684586298239947245938479716304835356329624224137216',
        ],
    'list': [
            '[]',
            '[3, 4, 5]',
            '[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]',
            '["a","b","c"]',
        ],
    'range': [
            'range(0)',
            'range(5)',
            'range(2, 7)',
            'range(2, 7, 2)',
            'range(7, 2, -1)',
            'range(7, 2, -2)',
        ],
    'set': [
            'set()',
            '{1, 2.3456, "another"}',
        ],
    'slice': [
            'slice(0)',
            'slice(5)',
            'slice(2, 7)',
            'slice(2, 7, 2)',
            'slice(7, 2, -1)',
            'slice(7, 2, -2)',
        ],
    'str': [
            '""',
            '"3"',
            '"This is another string"',
            '"Mÿ hôvèrçràft îß fûłl öf éêlś"',
            '"One arg: %s"',
            '"Three args: %s | %s | %s"',
        ],
    'tuple': [
            '()',
            '(False,)',
            '(1,)',
            '(1, 2)',
            '(1, 2, 3, 4, 5, 6, 7, 8, 9, 10)',
            '(3, 1.2, True, )',
            '(1, 2.3456, "another")',
        ],
    'None': [
            'None',
        ],
    'NotImplemented': [
            'NotImplemented',
        ],
}


SAMPLE_SUBSTITUTIONS = {
    # Normalize set ordering
    "{1, 2.3456, 'another'}": [
        "{1, 'another', 2.3456}",
        "{2.3456, 1, 'another'}",
        "{2.3456, 'another', 1}",
        "{'another', 1, 2.3456}",
        "{'another', 2.3456, 1}",
    ],
    "{'a', 'b', 'c'}": [
        "{'a', 'c', 'b'}",
        "{'b', 'a', 'c'}",
        "{'b', 'c', 'a'}",
        "{'c', 'a', 'b'}",
        "{'c', 'b', 'a'}",
    ],
    # Normalize dictionary ordering
    "{'a': 1, 'c': 2.3456, 'd': 'another'}": [
        "{'a': 1, 'd': 'another', 'c': 2.3456}",
        "{'c': 2.3456, 'd': 'another', 'a': 1}",
        "{'c': 2.3456, 'a': 1, 'd': 'another'}",
        "{'d': 'another', 'a': 1, 'c': 2.3456}",
        "{'d': 'another', 'c': 2.3456, 'a': 1}",
    ],
    # Normalize precision error
    "-0.00000265358979335273": ["-2.65358979335273e-6",],
    "-0.0000026535897933527304": ["-2.6535897933527304e-6",],
    "-0.9950547536867306": ["-0.9950547536867306",],
    "-0.9950547536867306": ["0.9950547536867305",],
    "-0.9962720564728149": ["-0.9962720564728148",],
    "-0.9981778976111988": ["-0.9981778976111987",],
    "-0.9950547536867306": ["-0.9950547536867305",],
    "-3.14159": ["-3.1415900000000008",],
    "0.0000026535897933620727": ["2.6535897933620727e-6",],
    "0.000002653589793362073": ["2.653589793362073e-6",],
    "0.000022090496998639075": ["2.2090496998585482e-5",],
    "0.0009093123056271857": ["0.000909312305627241",],
    "0.15729920705028488": ["0.157299207050285",],
    "0.6532125137753436": ["0.6532125137753437",],
    "0.6989700043360187": ["0.6989700043360189",],
    "0.7403626894942438": ["0.7403626894942439"],
    "0.842700792949715": ["0.8427007929497151",],
    "0.8813735870195429": ["0.881373587019543",],
    "0.9818155173002924": ["0.9818155173002925",],
    "0.9950547536867306": ["0.9950547536867305",],
    "0.9999665971563039": ["0.9999665971563038",],
    "1.5374368445009168e-12": ["1.5374597944280341e-12",],
    "1.584962500721156": ["1.5849625007211563",],
    "1.718281828459045": ["1.7182818284590453",],
    "1.9661605676901672e-10": ["1.9661604415428865e-10",],
    "1.9999779095030012": ["1.9999779095030015",],
    "11.591922629945447": ["11.591922629945449",],
    "18.964889726830812": ["18.964889726830815",],
    "19.265919722494793": ["19.265919722494797",],
    "160978210179491620.0": ["1.6097821017949162e+17",],
    "2.169925001442312": ["2.1699250014423126",],
    "308.2547155599167": ["308.25471555991675",],
    "321956420358983230.0": ["3.2195642035898323e+17",],
    "39.13389943631755": ["39.133899436317556",],
    "5.267662140304228": ["5.267662140304229",],
    "61.83553558589159": ["61.8355355858916",],
    "7.327471962526033e-15": ["7.357847917974392e-15",],
}


def _unary_test(test_name, operation):
    def func(self):
        self.assertUnaryOperation(
            x_values=SAMPLE_DATA[self.data_type],
            operation=operation,
            format=self.format,
            substitutions=SAMPLE_SUBSTITUTIONS
        )
    return func


class UnaryOperationTestCase(NotImplementedToExpectedFailure):
    format = ''

    @classmethod
    def tearDownClass(cls):
        global _phantomjs
        if _phantomjs:
            _phantomjs.kill()
            _phantomjs.stdin.close()
            _phantomjs.stdout.close()
            _phantomjs = None

    def assertUnaryOperation(self, x_values, operation, format, substitutions):
        self.assertCodeExecution(
            '##################################################\n'.join(
                adjust("""
                    try:
                        print('>>> x = %(x)s')
                        print('>>> %(format)s%(operation)sx')
                        x = %(x)s
                        print(%(format)s%(operation)sx)
                    except Exception as e:
                        print(type(e), ':', e)
                    print()
                    """ % {
                        'x': x,
                        'operation': operation,
                        'format': format,
                    }
                )
                for x in x_values
            ),
            "Error running %s" % operation,
            substitutions=substitutions
        )

    test_unary_positive = _unary_test('test_unary_positive', '+')
    test_unary_negative = _unary_test('test_unary_negative', '-')
    test_unary_not = _unary_test('test_unary_not', 'not ')
    test_unary_invert = _unary_test('test_unary_invert', '~')


def _binary_test(test_name, operation, examples, small_ints=False):
    def func(self):
        # CPython will attempt to malloc itself to death for some operations,
        # e.g., 1 << (2**32)
        # so we have this dirty hack
        actuals = examples
        if small_ints and test_name.endswith('_int'):
            actuals = [x for x in examples if abs(int(x)) < 8192]
        self.assertBinaryOperation(
            x_values=SAMPLE_DATA[self.data_type],
            y_values=actuals,
            operation=operation,
            format=self.format,
            substitutions=SAMPLE_SUBSTITUTIONS
        )
    return func


class BinaryOperationTestCase(NotImplementedToExpectedFailure):
    format = ''
    y = 3

    @classmethod
    def tearDownClass(cls):
        global _phantomjs
        if _phantomjs:
            _phantomjs.kill()
            _phantomjs.stdin.close()
            _phantomjs.stdout.close()
            _phantomjs = None

    def assertBinaryOperation(self, x_values, y_values, operation, format, substitutions):
        data = []
        for x in x_values:
            for y in y_values:
                data.append((x, y))

        self.assertCodeExecution(
            '##################################################\n'.join(
                adjust("""
                    try:
                        print('>>> x = %(x)s')
                        print('>>> y = %(y)s')
                        print('>>> %(format)s%(operation)s')
                        x = %(x)s
                        y = %(y)s
                        print(%(format)s%(operation)s)
                    except Exception as e:
                        print(type(e), ':', e)
                    print()
                    """ % {
                        'x': x,
                        'y': y,
                        'operation': operation,
                        'format': format,
                    }
                )
                for x, y in data
            ),
            "Error running %s" % operation,
            substitutions=substitutions
        )

    for datatype, examples in SAMPLE_DATA.items():
        vars()['test_add_%s' % datatype] = _binary_test('test_add_%s' % datatype, 'x + y', examples)
        vars()['test_subtract_%s' % datatype] = _binary_test('test_subtract_%s' % datatype, 'x - y', examples)
        vars()['test_multiply_%s' % datatype] = _binary_test('test_multiply_%s' % datatype, 'x * y', examples, small_ints=True)
        vars()['test_floor_divide_%s' % datatype] = _binary_test('test_floor_divide_%s' % datatype, 'x // y', examples)
        vars()['test_true_divide_%s' % datatype] = _binary_test('test_true_divide_%s' % datatype, 'x / y', examples)
        vars()['test_modulo_%s' % datatype] = _binary_test('test_modulo_%s' % datatype, 'x % y', examples)
        vars()['test_power_%s' % datatype] = _binary_test('test_power_%s' % datatype, 'x ** y', examples, small_ints=True)
        vars()['test_subscr_%s' % datatype] = _binary_test('test_subscr_%s' % datatype, 'x[y]', examples)
        vars()['test_lshift_%s' % datatype] = _binary_test('test_lshift_%s' % datatype, 'x << y', examples, small_ints=True)
        vars()['test_rshift_%s' % datatype] = _binary_test('test_rshift_%s' % datatype, 'x >> y', examples, small_ints=True)
        vars()['test_and_%s' % datatype] = _binary_test('test_and_%s' % datatype, 'x & y', examples)
        vars()['test_xor_%s' % datatype] = _binary_test('test_xor_%s' % datatype, 'x ^ y', examples)
        vars()['test_or_%s' % datatype] = _binary_test('test_or_%s' % datatype, 'x | y', examples)

        vars()['test_lt_%s' % datatype] = _binary_test('test_lt_%s' % datatype, 'x < y', examples)
        vars()['test_le_%s' % datatype] = _binary_test('test_le_%s' % datatype, 'x <= y', examples)
        vars()['test_gt_%s' % datatype] = _binary_test('test_gt_%s' % datatype, 'x > y', examples)
        vars()['test_ge_%s' % datatype] = _binary_test('test_ge_%s' % datatype, 'x >= y', examples)
        vars()['test_eq_%s' % datatype] = _binary_test('test_eq_%s' % datatype, 'x == y', examples)
        vars()['test_ne_%s' % datatype] = _binary_test('test_ne_%s' % datatype, 'x != y', examples)


def _inplace_test(test_name, operation, examples, small_ints=False):
    def func(self):
        actuals = examples
        if small_ints and test_name.endswith('_int'):
            actuals = [x for x in examples if abs(int(x)) < 8192]
        self.assertInplaceOperation(
            x_values=SAMPLE_DATA[self.data_type],
            y_values=actuals,
            operation=operation,
            format=self.format,
            substitutions=SAMPLE_SUBSTITUTIONS,
        )
    return func


class InplaceOperationTestCase(NotImplementedToExpectedFailure):
    format = ''
    y = 3

    @classmethod
    def tearDownClass(cls):
        global _phantomjs
        if _phantomjs:
            _phantomjs.kill()
            _phantomjs.stdin.close()
            _phantomjs.stdout.close()
            _phantomjs = None

    def assertInplaceOperation(self, x_values, y_values, operation, format, substitutions):
        data = []
        for x in x_values:
            for y in y_values:
                data.append((x, y))

        self.assertCodeExecution(
            '##################################################\n'.join(
                adjust("""
                    try:
                        print('>>> x = %(x)s')
                        print('>>> y = %(y)s')
                        print('>>> %(operation)s')
                        print('>>> %(format)sx')
                        x = %(x)s
                        y = %(y)s
                        %(operation)s
                        print(%(format)sx)
                    except Exception as e:
                        print(type(e), ':', e)
                    print()
                    """ % {
                        'x': x,
                        'y': y,
                        'operation': operation,
                        'format': format,
                    }
                )
                for x, y in data
            ),
            "Error running %s" % operation,
            substitutions=substitutions
        )

    for datatype, examples in SAMPLE_DATA.items():
        vars()['test_add_%s' % datatype] = _inplace_test('test_add_%s' % datatype, 'x += y', examples)
        vars()['test_subtract_%s' % datatype] = _inplace_test('test_subtract_%s' % datatype, 'x -= y', examples)
        vars()['test_multiply_%s' % datatype] = _inplace_test('test_multiply_%s' % datatype, 'x *= y', examples, small_ints=True)
        vars()['test_floor_divide_%s' % datatype] = _inplace_test('test_floor_divide_%s' % datatype, 'x //= y', examples)
        vars()['test_true_divide_%s' % datatype] = _inplace_test('test_true_divide_%s' % datatype, 'x /= y', examples)
        vars()['test_modulo_%s' % datatype] = _inplace_test('test_modulo_%s' % datatype, 'x %= y', examples)
        vars()['test_power_%s' % datatype] = _inplace_test('test_power_%s' % datatype, 'x **= y', examples, small_ints=True)
        vars()['test_lshift_%s' % datatype] = _inplace_test('test_lshift_%s' % datatype, 'x <<= y', examples, small_ints=True)
        vars()['test_rshift_%s' % datatype] = _inplace_test('test_rshift_%s' % datatype, 'x >>= y', examples)
        vars()['test_and_%s' % datatype] = _inplace_test('test_and_%s' % datatype, 'x &= y', examples)
        vars()['test_xor_%s' % datatype] = _inplace_test('test_xor_%s' % datatype, 'x ^= y', examples)
        vars()['test_or_%s' % datatype] = _inplace_test('test_or_%s' % datatype, 'x |= y', examples)


def _builtin_test(test_name, operation, examples):
    def func(self):
        self.assertBuiltinFunction(
            x_values=examples,
            f_values=self.functions,
            operation=operation,
            format=self.format,
            substitutions=SAMPLE_SUBSTITUTIONS
        )
    return func


class BuiltinFunctionTestCase(NotImplementedToExpectedFailure):
    format = ''

    @classmethod
    def tearDownClass(cls):
        global _phantomjs
        if _phantomjs:
            _phantomjs.kill()
            _phantomjs.stdin.close()
            _phantomjs.stdout.close()
            _phantomjs = None

    def assertBuiltinFunction(self, f_values, x_values, operation, format, substitutions):
        data = []
        for f in f_values:
            for x in x_values:
                data.append((f, x))

        self.assertCodeExecution(
            '##################################################\n'.join(
                adjust("""
                    try:
                        print('>>> f = %(f)s')
                        print('>>> x = %(x)s')
                        print('>>> %(format)s%(operation)s')
                        f = %(f)s
                        x = %(x)s
                        print(%(format)s%(operation)s)
                    except Exception as e:
                        print(type(e), ':', e)
                    print()
                    """ % {
                        'f': f,
                        'x': x,
                        'operation': operation,
                        'format': format,
                    }
                )
                for f, x in data
            ),
            "Error running %s" % operation,
            substitutions=substitutions
        )

    for datatype, examples in SAMPLE_DATA.items():
        vars()['test_%s' % datatype] = _builtin_test('test_%s' % datatype, 'f(x)', examples)


def _builtin_twoarg_test(test_name, operation, examples1, examples2):
    def func(self):
        self.assertBuiltinTwoargFunction(
            f_values=self.functions,
            x_values=examples1,
            y_values=examples2,
            operation=operation,
            format=self.format,
            substitutions=SAMPLE_SUBSTITUTIONS
        )
    return func


class BuiltinTwoargFunctionTestCase(NotImplementedToExpectedFailure):
    format = ''

    @classmethod
    def tearDownClass(cls):
        global _phantomjs
        if _phantomjs:
            _phantomjs.kill()
            _phantomjs.stdin.close()
            _phantomjs.stdout.close()
            _phantomjs = None

    def assertBuiltinTwoargFunction(self, f_values, x_values, y_values, operation, format, substitutions):
        data = []
        for f in f_values:
            for x in x_values:
                for y in y_values:
                    data.append((f, x, y))

        # filter out very large integers for some operations so as not
        # to crash CPython
        data = [(f, x, y) for f, x, y in data if not
            (f == 'pow' and
             x.lstrip('-').isdigit() and
             y.lstrip('-').isdigit() and
             (abs(int(x)) > 8192 or abs(int(y)) > 8192))]

        self.assertCodeExecution(
            '##################################################\n'.join(
                adjust("""
                    try:
                        print('>>> f = %(f)s')
                        print('>>> x = %(x)s')
                        print('>>> y = %(y)s')
                        print('>>> %(format)s%(operation)s')
                        f = %(f)s
                        x = %(x)s
                        y = %(y)s
                        print(%(format)s%(operation)s)
                    except Exception as e:
                        print(type(e), ':', e)
                    print()
                    """ % {
                        'f': f,
                        'x': x,
                        'y': y,
                        'operation': operation,
                        'format': format,
                    }
                )
                for f, x, y in data
            ),
            "Error running %s" % operation,
            substitutions=substitutions
        )

    for datatype1, examples1 in SAMPLE_DATA.items():
        for datatype2, examples2 in SAMPLE_DATA.items():
            vars()['test_%s_%s' % (datatype1, datatype2)] = _builtin_twoarg_test(
                'test_%s_%s' % (datatype1, datatype2),
                'f(x, y)', examples1, examples2
            )


def _module_one_arg_func_test(name, module, f, examples, small_ints=False):
    # Factorials can make us run out of memory and crash.
    # so we have this dirty hack
    actuals = examples
    if small_ints and name.endswith('_int'):
        actuals = [x for x in examples if abs(int(x)) < 8192]
    def func(self):
        self.assertOneArgModuleFuction(
            name=name,
            module=module,
            func=f,
            x_values=actuals,
            substitutions=SAMPLE_SUBSTITUTIONS
        )
    return func

def _module_two_arg_func_test(name, module, f,  examples, examples2):
    def func(self):
        self.assertTwoArgModuleFuction(
            name=name,
            module=module,
            func=f,
            x_values=examples,
            y_values=examples2,
            substitutions=SAMPLE_SUBSTITUTIONS
        )
    return func

class ModuleFunctionTestCase(NotImplementedToExpectedFailure):
    @classmethod
    def tearDownClass(cls):
        global _phantomjs
        if _phantomjs:
            _phantomjs.kill()
            _phantomjs.stdin.close()
            _phantomjs.stdout.close()
            _phantomjs = None

    def assertOneArgModuleFuction(self, name, module, func, x_values, substitutions):
        self.assertCodeExecution(
            '##################################################\n'.join(
                adjust("""
                    try:
                        print('>>> import %(m)s')
                        print('>>> f = %(m)s.%(f)s')
                        print('>>> x = %(x)s')
                        print('>>> f(x)')
                        import %(m)s
                        f = %(m)s.%(f)s
                        x = %(x)s
                        print(f(x))
                    except Exception as e:
                        print(type(e), ':', e)
                    print()
                    """ % {
                        'f': func,
                        'x': x,
                        'm': module,
                    }
                )
                for x in x_values
            ),
            "Error running %s module %s" % (module, name),
            substitutions=substitutions
        )

    def assertTwoArgModuleFuction(self, name, module, func, x_values, y_values, substitutions):
        self.assertCodeExecution(
            '##################################################\n'.join(
                adjust("""
                    try:
                        print('>>> import %(m)s')
                        print('>>> f = %(m)s.%(f)s')
                        print('>>> x = %(x)s')
                        print('>>> y = %(y)s')
                        print('>>> f(x, y)')
                        import %(m)s
                        f = %(m)s.%(f)s
                        x = %(x)s
                        x = %(y)s
                        print(f(x, y))
                    except Exception as e:
                        print(type(e), ':', e)
                    print()
                    """ % {
                        'f': func,
                        'x': x,
                        'y': y,
                        'm': module,
                    }
                )
                for x in x_values for y in y_values
            ),
            "Error running %s module %s" % (module, name),
            substitutions=substitutions)

    @classmethod
    def add_one_arg_tests(self, module, functions):
        for func in functions:
            for datatype, examples in SAMPLE_DATA.items():
                name = 'test_%s_%s_%s' % (module, func, datatype)
                small_ints = module == 'math' and func == 'factorial'
                setattr(self, name, _module_one_arg_func_test(name, 'math', func, examples, small_ints=small_ints))

    @classmethod
    def add_two_arg_tests(self, module, functions):
        for func in functions:
            for datatype, examples in SAMPLE_DATA.items():
                for datatype2, examples2 in SAMPLE_DATA.items():
                    name = 'test_%s_%s_%s_%s' % (module, func, datatype, datatype2)
                    setattr(self, name, _module_two_arg_func_test(name, 'math', func, examples, examples2))
