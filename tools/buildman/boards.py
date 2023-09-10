# SPDX-License-Identifier: GPL-2.0+
# Copyright (c) 2012 The Chromium OS Authors.
# Author: Simon Glass <sjg@chromium.org>
# Author: Masahiro Yamada <yamada.m@jp.panasonic.com>

"""Maintains a list of boards and allows them to be selected"""

from collections import OrderedDict
import errno
import fnmatch
import glob
import multiprocessing
import os
import re
import sys
import tempfile
import time

from buildman import board
from buildman import kconfiglib


### constant variables ###
OUTPUT_FILE = 'boards.cfg'
CONFIG_DIR = 'configs'
SLEEP_TIME = 0.03
COMMENT_BLOCK = f'''#
# List of boards
#   Automatically generated by {__file__}: don't edit
#
# Status, Arch, CPU, SoC, Vendor, Board, Target, Config, Maintainers

'''


def try_remove(fname):
    """Remove a file ignoring 'No such file or directory' error.

    Args:
        fname (str): Filename to remove

    Raises:
        OSError: output file exists but could not be removed
    """
    try:
        os.remove(fname)
    except OSError as exception:
        # Ignore 'No such file or directory' error
        if exception.errno != errno.ENOENT:
            raise


def output_is_new(output, config_dir, srcdir):
    """Check if the output file is up to date.

    Looks at defconfig and Kconfig files to make sure none is newer than the
    output file. Also ensures that the boards.cfg does not mention any removed
    boards.

    Args:
        output (str): Filename to check
        config_dir (str): Directory containing defconfig files
        srcdir (str): Directory containing Kconfig and MAINTAINERS files

    Returns:
        True if the given output file exists and is newer than any of
        *_defconfig, MAINTAINERS and Kconfig*.  False otherwise.

    Raises:
        OSError: output file exists but could not be opened
    """
    # pylint: disable=too-many-branches
    try:
        ctime = os.path.getctime(output)
    except OSError as exception:
        if exception.errno == errno.ENOENT:
            # return False on 'No such file or directory' error
            return False
        raise

    for (dirpath, _, filenames) in os.walk(config_dir):
        for filename in fnmatch.filter(filenames, '*_defconfig'):
            if fnmatch.fnmatch(filename, '.*'):
                continue
            filepath = os.path.join(dirpath, filename)
            if ctime < os.path.getctime(filepath):
                return False

    for (dirpath, _, filenames) in os.walk(srcdir):
        for filename in filenames:
            if (fnmatch.fnmatch(filename, '*~') or
                not fnmatch.fnmatch(filename, 'Kconfig*') and
                not filename == 'MAINTAINERS'):
                continue
            filepath = os.path.join(dirpath, filename)
            if ctime < os.path.getctime(filepath):
                return False

    # Detect a board that has been removed since the current board database
    # was generated
    with open(output, encoding="utf-8") as inf:
        for line in inf:
            if 'Options,' in line:
                return False
            if line[0] == '#' or line == '\n':
                continue
            defconfig = line.split()[6] + '_defconfig'
            if not os.path.exists(os.path.join(config_dir, defconfig)):
                return False

    return True


class Expr:
    """A single regular expression for matching boards to build"""

    def __init__(self, expr):
        """Set up a new Expr object.

        Args:
            expr (str): String cotaining regular expression to store
        """
        self._expr = expr
        self._re = re.compile(expr)

    def matches(self, props):
        """Check if any of the properties match the regular expression.

        Args:
           props (list of str): List of properties to check
        Returns:
           True if any of the properties match the regular expression
        """
        for prop in props:
            if self._re.match(prop):
                return True
        return False

    def __str__(self):
        return self._expr

class Term:
    """A list of expressions each of which must match with properties.

    This provides a list of 'AND' expressions, meaning that each must
    match the board properties for that board to be built.
    """
    def __init__(self):
        self._expr_list = []
        self._board_count = 0

    def add_expr(self, expr):
        """Add an Expr object to the list to check.

        Args:
            expr (Expr): New Expr object to add to the list of those that must
                  match for a board to be built.
        """
        self._expr_list.append(Expr(expr))

    def __str__(self):
        """Return some sort of useful string describing the term"""
        return '&'.join([str(expr) for expr in self._expr_list])

    def matches(self, props):
        """Check if any of the properties match this term

        Each of the expressions in the term is checked. All must match.

        Args:
           props (list of str): List of properties to check
        Returns:
           True if all of the expressions in the Term match, else False
        """
        for expr in self._expr_list:
            if not expr.matches(props):
                return False
        return True


class KconfigScanner:

    """Kconfig scanner."""

    ### constant variable only used in this class ###
    _SYMBOL_TABLE = {
        'arch' : 'SYS_ARCH',
        'cpu' : 'SYS_CPU',
        'soc' : 'SYS_SOC',
        'vendor' : 'SYS_VENDOR',
        'board' : 'SYS_BOARD',
        'config' : 'SYS_CONFIG_NAME',
        # 'target' is added later
    }

    def __init__(self, srctree):
        """Scan all the Kconfig files and create a Kconfig object."""
        # Define environment variables referenced from Kconfig
        os.environ['srctree'] = srctree
        os.environ['UBOOTVERSION'] = 'dummy'
        os.environ['KCONFIG_OBJDIR'] = ''
        self._tmpfile = None
        self._conf = kconfiglib.Kconfig(warn=False)

    def __del__(self):
        """Delete a leftover temporary file before exit.

        The scan() method of this class creates a temporay file and deletes
        it on success.  If scan() method throws an exception on the way,
        the temporary file might be left over.  In that case, it should be
        deleted in this destructor.
        """
        if self._tmpfile:
            try_remove(self._tmpfile)

    def scan(self, defconfig, warn_targets):
        """Load a defconfig file to obtain board parameters.

        Args:
            defconfig (str): path to the defconfig file to be processed
            warn_targets (bool): True to warn about missing or duplicate
                CONFIG_TARGET options

        Returns:
            tuple: dictionary of board parameters.  It has a form of:
                {
                    'arch': <arch_name>,
                    'cpu': <cpu_name>,
                    'soc': <soc_name>,
                    'vendor': <vendor_name>,
                    'board': <board_name>,
                    'target': <target_name>,
                    'config': <config_header_name>,
                }
            warnings (list of str): list of warnings found
        """
        leaf = os.path.basename(defconfig)
        expect_target, match, rear = leaf.partition('_defconfig')
        assert match and not rear, f'{leaf} : invalid defconfig'

        self._conf.load_config(defconfig)
        self._tmpfile = None

        params = {}
        warnings = []

        # Get the value of CONFIG_SYS_ARCH, CONFIG_SYS_CPU, ... etc.
        # Set '-' if the value is empty.
        for key, symbol in list(self._SYMBOL_TABLE.items()):
            value = self._conf.syms.get(symbol).str_value
            if value:
                params[key] = value
            else:
                params[key] = '-'

        # Check there is exactly one TARGET_xxx set
        if warn_targets:
            target = None
            for name, sym in self._conf.syms.items():
                if name.startswith('TARGET_') and sym.str_value == 'y':
                    tname = name[7:].lower()
                    if target:
                        warnings.append(
                            f'WARNING: {leaf}: Duplicate TARGET_xxx: {target} and {tname}')
                    else:
                        target = tname

            if not target:
                cfg_name = expect_target.replace('-', '_').upper()
                warnings.append(f'WARNING: {leaf}: No TARGET_{cfg_name} enabled')

        params['target'] = expect_target

        # fix-up for aarch64
        if params['arch'] == 'arm' and params['cpu'] == 'armv8':
            params['arch'] = 'aarch64'

        # fix-up for riscv
        if params['arch'] == 'riscv':
            try:
                value = self._conf.syms.get('ARCH_RV32I').str_value
            except:
                value = ''
            if value == 'y':
                params['arch'] = 'riscv32'
            else:
                params['arch'] = 'riscv64'

        return params, warnings


class MaintainersDatabase:

    """The database of board status and maintainers.

    Properties:
        database: dict:
            key: Board-target name (e.g. 'snow')
            value: tuple:
                str: Board status (e.g. 'Active')
                str: List of maintainers, separated by :
        warnings (list of str): List of warnings due to missing status, etc.
    """

    def __init__(self):
        """Create an empty database."""
        self.database = {}
        self.warnings = []

    def get_status(self, target):
        """Return the status of the given board.

        The board status is generally either 'Active' or 'Orphan'.
        Display a warning message and return '-' if status information
        is not found.

        Args:
            target (str): Build-target name

        Returns:
            str: 'Active', 'Orphan' or '-'.
        """
        if not target in self.database:
            self.warnings.append(f"WARNING: no status info for '{target}'")
            return '-'

        tmp = self.database[target][0]
        if tmp.startswith('Maintained'):
            return 'Active'
        if tmp.startswith('Supported'):
            return 'Active'
        if tmp.startswith('Orphan'):
            return 'Orphan'
        self.warnings.append(f"WARNING: {tmp}: unknown status for '{target}'")
        return '-'

    def get_maintainers(self, target):
        """Return the maintainers of the given board.

        Args:
            target (str): Build-target name

        Returns:
            str: Maintainers of the board.  If the board has two or more
            maintainers, they are separated with colons.
        """
        entry = self.database.get(target)
        if entry:
            status, maint_list = entry
            if not status.startswith('Orphan'):
                if len(maint_list) > 1 or (maint_list and maint_list[0] != '-'):
                    return ':'.join(maint_list)

        self.warnings.append(f"WARNING: no maintainers for '{target}'")
        return ''

    def parse_file(self, srcdir, fname):
        """Parse a MAINTAINERS file.

        Parse a MAINTAINERS file and accumulate board status and maintainers
        information in the self.database dict.

        defconfig files are used to specify the target, e.g. xxx_defconfig is
        used for target 'xxx'. If there is no defconfig file mentioned in the
        MAINTAINERS file F: entries, then this function does nothing.

        The N: name entries can be used to specify a defconfig file using
        wildcards.

        Args:
            srcdir (str): Directory containing source code (Kconfig files)
            fname (str): MAINTAINERS file to be parsed
        """
        def add_targets(linenum):
            """Add any new targets

            Args:
                linenum (int): Current line number
            """
            if targets:
                for target in targets:
                    self.database[target] = (status, maintainers)

        targets = []
        maintainers = []
        status = '-'
        with open(fname, encoding="utf-8") as inf:
            for linenum, line in enumerate(inf):
                # Check also commented maintainers
                if line[:3] == '#M:':
                    line = line[1:]
                tag, rest = line[:2], line[2:].strip()
                if tag == 'M:':
                    maintainers.append(rest)
                elif tag == 'F:':
                    # expand wildcard and filter by 'configs/*_defconfig'
                    glob_path = os.path.join(srcdir, rest)
                    for item in glob.glob(glob_path):
                        front, match, rear = item.partition('configs/')
                        if front.endswith('/'):
                            front = front[:-1]
                        if front == srcdir and match:
                            front, match, rear = rear.rpartition('_defconfig')
                            if match and not rear:
                                targets.append(front)
                elif tag == 'S:':
                    status = rest
                elif tag == 'N:':
                    # Just scan the configs directory since that's all we care
                    # about
                    walk_path = os.walk(os.path.join(srcdir, 'configs'))
                    for dirpath, _, fnames in walk_path:
                        for cfg in fnames:
                            path = os.path.join(dirpath, cfg)[len(srcdir) + 1:]
                            front, match, rear = path.partition('configs/')
                            if front or not match:
                                continue
                            front, match, rear = rear.rpartition('_defconfig')

                            # Use this entry if it matches the defconfig file
                            # without the _defconfig suffix. For example
                            # 'am335x.*' matches am335x_guardian_defconfig
                            if match and not rear and re.search(rest, front):
                                targets.append(front)
                elif line == '\n':
                    add_targets(linenum)
                    targets = []
                    maintainers = []
                    status = '-'
        add_targets(linenum)


class Boards:
    """Manage a list of boards."""
    def __init__(self):
        self._boards = []

    def add_board(self, brd):
        """Add a new board to the list.

        The board's target member must not already exist in the board list.

        Args:
            brd (Board): board to add
        """
        self._boards.append(brd)

    def read_boards(self, fname):
        """Read a list of boards from a board file.

        Create a Board object for each and add it to our _boards list.

        Args:
            fname (str): Filename of boards.cfg file
        """
        with open(fname, 'r', encoding='utf-8') as inf:
            for line in inf:
                if line[0] == '#':
                    continue
                fields = line.split()
                if not fields:
                    continue
                for upto, field in enumerate(fields):
                    if field == '-':
                        fields[upto] = ''
                while len(fields) < 8:
                    fields.append('')
                if len(fields) > 8:
                    fields = fields[:8]

                brd = board.Board(*fields)
                self.add_board(brd)


    def get_list(self):
        """Return a list of available boards.

        Returns:
            List of Board objects
        """
        return self._boards

    def get_dict(self):
        """Build a dictionary containing all the boards.

        Returns:
            Dictionary:
                key is board.target
                value is board
        """
        board_dict = OrderedDict()
        for brd in self._boards:
            board_dict[brd.target] = brd
        return board_dict

    def get_selected_dict(self):
        """Return a dictionary containing the selected boards

        Returns:
            List of Board objects that are marked selected
        """
        board_dict = OrderedDict()
        for brd in self._boards:
            if brd.build_it:
                board_dict[brd.target] = brd
        return board_dict

    def get_selected(self):
        """Return a list of selected boards

        Returns:
            List of Board objects that are marked selected
        """
        return [brd for brd in self._boards if brd.build_it]

    def get_selected_names(self):
        """Return a list of selected boards

        Returns:
            List of board names that are marked selected
        """
        return [brd.target for brd in self._boards if brd.build_it]

    @classmethod
    def _build_terms(cls, args):
        """Convert command line arguments to a list of terms.

        This deals with parsing of the arguments. It handles the '&'
        operator, which joins several expressions into a single Term.

        For example:
            ['arm & freescale sandbox', 'tegra']

        will produce 3 Terms containing expressions as follows:
            arm, freescale
            sandbox
            tegra

        The first Term has two expressions, both of which must match for
        a board to be selected.

        Args:
            args (list of str): List of command line arguments

        Returns:
            list of Term: A list of Term objects
        """
        syms = []
        for arg in args:
            for word in arg.split():
                sym_build = []
                for term in word.split('&'):
                    if term:
                        sym_build.append(term)
                    sym_build.append('&')
                syms += sym_build[:-1]
        terms = []
        term = None
        oper = None
        for sym in syms:
            if sym == '&':
                oper = sym
            elif oper:
                term.add_expr(sym)
                oper = None
            else:
                if term:
                    terms.append(term)
                term = Term()
                term.add_expr(sym)
        if term:
            terms.append(term)
        return terms

    def select_boards(self, args, exclude=None, brds=None):
        """Mark boards selected based on args

        Normally either boards (an explicit list of boards) or args (a list of
        terms to match against) is used. It is possible to specify both, in
        which case they are additive.

        If brds and args are both empty, all boards are selected.

        Args:
            args (list of str): List of strings specifying boards to include,
                either named, or by their target, architecture, cpu, vendor or
                soc. If empty, all boards are selected.
            exclude (list of str): List of boards to exclude, regardless of
                'args', or None for none
            brds (list of Board): List of boards to build, or None/[] for all

        Returns:
            Tuple
                Dictionary which holds the list of boards which were selected
                    due to each argument, arranged by argument.
                List of errors found
        """
        def _check_board(brd):
            """Check whether to include or exclude a board

            Checks the various terms and decide whether to build it or not (the
            'build_it' variable).

            If it is built, add the board to the result[term] list so we know
            which term caused it to be built. Add it to result['all'] also.

            Keep a list of boards we found in 'found', so we can report boards
            which appear in self._boards but not in brds.

            Args:
                brd (Board): Board to check
            """
            matching_term = None
            build_it = False
            if terms:
                for term in terms:
                    if term.matches(brd.props):
                        matching_term = str(term)
                        build_it = True
                        break
            elif brds:
                if brd.target in brds:
                    build_it = True
                    found.append(brd.target)
            else:
                build_it = True

            # Check that it is not specifically excluded
            for expr in exclude_list:
                if expr.matches(brd.props):
                    build_it = False
                    break

            if build_it:
                brd.build_it = True
                if matching_term:
                    result[matching_term].append(brd.target)
                result['all'].append(brd.target)

        result = OrderedDict()
        warnings = []
        terms = self._build_terms(args)

        result['all'] = []
        for term in terms:
            result[str(term)] = []

        exclude_list = []
        if exclude:
            for expr in exclude:
                exclude_list.append(Expr(expr))

        found = []
        for brd in self._boards:
            _check_board(brd)

        if brds:
            remaining = set(brds) - set(found)
            if remaining:
                warnings.append(f"Boards not found: {', '.join(remaining)}\n")

        return result, warnings

    @classmethod
    def scan_defconfigs_for_multiprocess(cls, srcdir, queue, defconfigs,
                                         warn_targets):
        """Scan defconfig files and queue their board parameters

        This function is intended to be passed to multiprocessing.Process()
        constructor.

        Args:
            srcdir (str): Directory containing source code
            queue (multiprocessing.Queue): The resulting board parameters are
                written into this.
            defconfigs (sequence of str): A sequence of defconfig files to be
                scanned.
            warn_targets (bool): True to warn about missing or duplicate
                CONFIG_TARGET options
        """
        kconf_scanner = KconfigScanner(srcdir)
        for defconfig in defconfigs:
            queue.put(kconf_scanner.scan(defconfig, warn_targets))

    @classmethod
    def read_queues(cls, queues, params_list, warnings):
        """Read the queues and append the data to the paramers list

        Args:
            queues (list of multiprocessing.Queue): Queues to read
            params_list (list of dict): List to add params too
            warnings (set of str): Set to add warnings to
        """
        for que in queues:
            while not que.empty():
                params, warn = que.get()
                params_list.append(params)
                warnings.update(warn)

    def scan_defconfigs(self, config_dir, srcdir, jobs=1, warn_targets=False):
        """Collect board parameters for all defconfig files.

        This function invokes multiple processes for faster processing.

        Args:
            config_dir (str): Directory containing the defconfig files
            srcdir (str): Directory containing source code (Kconfig files)
            jobs (int): The number of jobs to run simultaneously
            warn_targets (bool): True to warn about missing or duplicate
                CONFIG_TARGET options

        Returns:
            tuple:
                list of dict: List of board parameters, each a dict:
                    key: 'arch', 'cpu', 'soc', 'vendor', 'board', 'target',
                        'config'
                    value: string value of the key
                list of str: List of warnings recorded
        """
        all_defconfigs = []
        for (dirpath, _, filenames) in os.walk(config_dir):
            for filename in fnmatch.filter(filenames, '*_defconfig'):
                if fnmatch.fnmatch(filename, '.*'):
                    continue
                all_defconfigs.append(os.path.join(dirpath, filename))

        total_boards = len(all_defconfigs)
        processes = []
        queues = []
        for i in range(jobs):
            defconfigs = all_defconfigs[total_boards * i // jobs :
                                        total_boards * (i + 1) // jobs]
            que = multiprocessing.Queue(maxsize=-1)
            proc = multiprocessing.Process(
                target=self.scan_defconfigs_for_multiprocess,
                args=(srcdir, que, defconfigs, warn_targets))
            proc.start()
            processes.append(proc)
            queues.append(que)

        # The resulting data should be accumulated to these lists
        params_list = []
        warnings = set()

        # Data in the queues should be retrieved preriodically.
        # Otherwise, the queues would become full and subprocesses would get stuck.
        while any(p.is_alive() for p in processes):
            self.read_queues(queues, params_list, warnings)
            # sleep for a while until the queues are filled
            time.sleep(SLEEP_TIME)

        # Joining subprocesses just in case
        # (All subprocesses should already have been finished)
        for proc in processes:
            proc.join()

        # retrieve leftover data
        self.read_queues(queues, params_list, warnings)

        return params_list, sorted(list(warnings))

    @classmethod
    def insert_maintainers_info(cls, srcdir, params_list):
        """Add Status and Maintainers information to the board parameters list.

        Args:
            params_list (list of dict): A list of the board parameters

        Returns:
            list of str: List of warnings collected due to missing status, etc.
        """
        database = MaintainersDatabase()
        for (dirpath, _, filenames) in os.walk(srcdir):
            if 'MAINTAINERS' in filenames and 'tools/buildman' not in dirpath:
                database.parse_file(srcdir,
                                    os.path.join(dirpath, 'MAINTAINERS'))

        for i, params in enumerate(params_list):
            target = params['target']
            maintainers = database.get_maintainers(target)
            params['maintainers'] = maintainers
            if maintainers:
                params['status'] = database.get_status(target)
            else:
                params['status'] = '-'
            params_list[i] = params
        return sorted(database.warnings)

    @classmethod
    def format_and_output(cls, params_list, output):
        """Write board parameters into a file.

        Columnate the board parameters, sort lines alphabetically,
        and then write them to a file.

        Args:
            params_list (list of dict): The list of board parameters
            output (str): The path to the output file
        """
        fields = ('status', 'arch', 'cpu', 'soc', 'vendor', 'board', 'target',
                  'config', 'maintainers')

        # First, decide the width of each column
        max_length = {f: 0 for f in fields}
        for params in params_list:
            for field in fields:
                max_length[field] = max(max_length[field], len(params[field]))

        output_lines = []
        for params in params_list:
            line = ''
            for field in fields:
                # insert two spaces between fields like column -t would
                line += '  ' + params[field].ljust(max_length[field])
            output_lines.append(line.strip())

        # ignore case when sorting
        output_lines.sort(key=str.lower)

        with open(output, 'w', encoding="utf-8") as outf:
            outf.write(COMMENT_BLOCK + '\n'.join(output_lines) + '\n')

    def build_board_list(self, config_dir=CONFIG_DIR, srcdir='.', jobs=1,
                         warn_targets=False):
        """Generate a board-database file

        This works by reading the Kconfig, then loading each board's defconfig
        in to get the setting for each option. In particular, CONFIG_TARGET_xxx
        is typically set by the defconfig, where xxx is the target to build.

        Args:
            config_dir (str): Directory containing the defconfig files
            srcdir (str): Directory containing source code (Kconfig files)
            jobs (int): The number of jobs to run simultaneously
            warn_targets (bool): True to warn about missing or duplicate
                CONFIG_TARGET options

        Returns:
            tuple:
                list of dict: List of board parameters, each a dict:
                    key: 'arch', 'cpu', 'soc', 'vendor', 'board', 'config',
                         'target'
                    value: string value of the key
                list of str: Warnings that came up
        """
        params_list, warnings = self.scan_defconfigs(config_dir, srcdir, jobs,
                                                     warn_targets)
        m_warnings = self.insert_maintainers_info(srcdir, params_list)
        return params_list, warnings + m_warnings

    def ensure_board_list(self, output, jobs=1, force=False, quiet=False):
        """Generate a board database file if needed.

        This is intended to check if Kconfig has changed since the boards.cfg
        files was generated.

        Args:
            output (str): The name of the output file
            jobs (int): The number of jobs to run simultaneously
            force (bool): Force to generate the output even if it is new
            quiet (bool): True to avoid printing a message if nothing needs doing

        Returns:
            bool: True if all is well, False if there were warnings
        """
        if not force and output_is_new(output, CONFIG_DIR, '.'):
            if not quiet:
                print(f'{output} is up to date. Nothing to do.')
            return True
        params_list, warnings = self.build_board_list(CONFIG_DIR, '.', jobs)
        for warn in warnings:
            print(warn, file=sys.stderr)
        self.format_and_output(params_list, output)
        return not warnings
