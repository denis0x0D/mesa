# Copyright © 2019-2020 Intel Corporation

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""Core data structures and routines for pick."""

import asyncio
import enum
import json
import pathlib
import re
import typing

import attr

if typing.TYPE_CHECKING:
    from .ui import UI

    import typing_extensions

    class CommitDict(typing_extensions.TypedDict):

        sha: str
        description: str
        nomintated: bool
        nomination_type: typing.Optional[int]
        resolution: typing.Optional[int]
        master_sha: typing.Optional[str]

IS_FIX = re.compile(r'^\s*fixes:\s*([a-f0-9]{6,40})', flags=re.MULTILINE | re.IGNORECASE)
# FIXME: I dislike the duplication in this regex, but I couldn't get it to work otherwise
IS_CC = re.compile(r'^\s*cc:\s*["\']?([0-9]{2}\.[0-9])?["\']?\s*["\']?([0-9]{2}\.[0-9])?["\']?\s*\<?mesa-stable',
                   flags=re.MULTILINE | re.IGNORECASE)
IS_REVERT = re.compile(r'This reverts commit ([0-9a-f]{40})')

# XXX: hack
SEM = asyncio.Semaphore(50)

COMMIT_LOCK = asyncio.Lock()


class PickUIException(Exception):
    pass


@enum.unique
class NominationType(enum.Enum):

    CC = 0
    FIXES = 1
    REVERT = 2


@enum.unique
class Resolution(enum.Enum):

    UNRESOLVED = 0
    MERGED = 1
    DENOMINATED = 2
    BACKPORTED = 3
    NOTNEEDED = 4


async def commit_state(*, amend: bool = False, message: str = 'Update') -> None:
    """Commit the .pick_status.json file."""
    f = pathlib.Path(__file__).parent.parent.parent / '.pick_status.json'
    async with COMMIT_LOCK:
        p = await asyncio.create_subprocess_exec(
            'git', 'add', f.as_posix(),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        v = await p.wait()
        if v != 0:
            return False

        if amend:
            cmd = ['--amend', '--no-edit']
        else:
            cmd = ['--message', f'.pick_status.json: {message}']
        p = await asyncio.create_subprocess_exec(
            'git', 'commit', *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        v = await p.wait()
        if v != 0:
            return False
    return True


@attr.s(slots=True)
class Commit:

    sha: str = attr.ib()
    description: str = attr.ib()
    nominated: bool = attr.ib(False)
    nomination_type: typing.Optional[NominationType] = attr.ib(None)
    resolution: Resolution = attr.ib(Resolution.UNRESOLVED)
    master_sha: typing.Optional[str] = attr.ib(None)
    because_sha: typing.Optional[str] = attr.ib(None)

    def to_json(self) -> 'CommitDict':
        d: typing.Dict[str, typing.Any] = attr.asdict(self)
        if self.nomination_type is not None:
            d['nomination_type'] = self.nomination_type.value
        if self.resolution is not None:
            d['resolution'] = self.resolution.value
        return typing.cast('CommitDict', d)

    @classmethod
    def from_json(cls, data: 'CommitDict') -> 'Commit':
        c = cls(data['sha'], data['description'], data['nominated'], master_sha=data['master_sha'], because_sha=data['because_sha'])
        if data['nomination_type'] is not None:
            c.nomination_type = NominationType(data['nomination_type'])
        if data['resolution'] is not None:
            c.resolution = Resolution(data['resolution'])
        return c

    async def apply(self, ui: 'UI') -> typing.Tuple[bool, str]:
        # FIXME: This isn't really enough if we fail to cherry-pick because the
        # git tree will still be dirty
        async with COMMIT_LOCK:
            p = await asyncio.create_subprocess_exec(
                'git', 'cherry-pick', '-x', self.sha,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, err = await p.communicate()

        if p.returncode != 0:
            return (False, err)

        self.resolution = Resolution.MERGED
        await ui.feedback(f'{self.sha} ({self.description}) applied successfully')

        # Append the changes to the .pickstatus.json file
        ui.save()
        v = await commit_state(amend=True)
        return (v, '')

    async def abort_cherry(self, ui: 'UI', err: str) -> None:
        await ui.feedback(f'{self.sha} ({self.description}) failed to apply\n{err}')
        async with COMMIT_LOCK:
            p = await asyncio.create_subprocess_exec(
                'git', 'cherry-pick', '--abort',
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            r = await p.wait()
        await ui.feedback(f'{"Successfully" if r == 0 else "Failed to"} abort cherry-pick.')

    async def denominate(self, ui: 'UI') -> bool:
        self.resolution = Resolution.DENOMINATED
        ui.save()
        v = await commit_state(message=f'Mark {self.sha} as denominated')
        assert v
        await ui.feedback(f'{self.sha} ({self.description}) denominated successfully')
        return True

    async def backport(self, ui: 'UI') -> bool:
        self.resolution = Resolution.BACKPORTED
        ui.save()
        v = await commit_state(message=f'Mark {self.sha} as backported')
        assert v
        await ui.feedback(f'{self.sha} ({self.description}) backported successfully')
        return True

    async def resolve(self, ui: 'UI') -> None:
        self.resolution = Resolution.MERGED
        ui.save()
        v = await commit_state(amend=True)
        assert v
        await ui.feedback(f'{self.sha} ({self.description}) committed successfully')


async def get_new_commits(sha: str) -> typing.List[typing.Tuple[str, str]]:
    # TODO: config file that points to the upstream branch
    p = await asyncio.create_subprocess_exec(
        'git', 'log', '--pretty=oneline', f'{sha}..master',
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL)
    out, _ = await p.communicate()
    assert p.returncode == 0, f"git log didn't work: {sha}"
    return list(split_commit_list(out.decode().strip()))


def split_commit_list(commits: str) -> typing.Generator[typing.Tuple[str, str], None, None]:
    if not commits:
        return
    for line in commits.split('\n'):
        v = tuple(line.split(' ', 1))
        assert len(v) == 2, 'this is really just for mypy'
        yield typing.cast(typing.Tuple[str, str], v)


async def is_commit_in_branch(sha: str) -> bool:
    async with SEM:
        p = await asyncio.create_subprocess_exec(
            'git', 'merge-base', '--is-ancestor', sha, 'HEAD',
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await p.wait()
    return p.returncode == 0


async def full_sha(sha: str) -> str:
    async with SEM:
        p = await asyncio.create_subprocess_exec(
            'git', 'rev-parse', sha,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await p.communicate()
    if p.returncode:
        raise PickUIException(f'Invalid Sha {sha}')
    return out.decode().strip()


async def resolve_nomination(commit: 'Commit', version: str) -> 'Commit':
    async with SEM:
        p = await asyncio.create_subprocess_exec(
            'git', 'log', '--pretty=medium', '-1', commit.sha,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        _out, _ = await p.communicate()
        assert p.returncode == 0, f'git log for {commit.sha} failed'
    out = _out.decode()

    # We give presedence to fixes and cc tags over revert tags.
    # XXX: not having the wallrus operator available makes me sad :=
    m = IS_FIX.search(out)
    if m:
        # We set the nomination_type and because_sha here so that we can later
        # check to see if this fixes another staged commit.
        try:
            commit.because_sha = fixed = await full_sha(m.group(1))
        except PickUIException:
            pass
        else:
            commit.nomination_type = NominationType.FIXES
            if await is_commit_in_branch(fixed):
                commit.nominated = True
                return commit

    m = IS_CC.search(out)
    if m:
        if m.groups() == (None, None) or version in m.groups():
            commit.nominated = True
            commit.nomination_type = NominationType.CC
            return commit

    m = IS_REVERT.search(out)
    if m:
        # See comment for IS_FIX path
        try:
            commit.because_sha = reverted = await full_sha(m.group(1))
        except PickUIException:
            pass
        else:
            commit.nomination_type = NominationType.REVERT
            if await is_commit_in_branch(reverted):
                commit.nominated = True
                return commit

    return commit


async def resolve_fixes(commits: typing.List['Commit'], previous: typing.List['Commit']) -> None:
    """Determine if any of the undecided commits fix/revert a staged commit.

    The are still needed if they apply to a commit that is staged for
    inclusion, but not yet included.

    This must be done in order, because a commit 3 might fix commit 2 which
    fixes commit 1.
    """
    shas: typing.Set[str] = set(c.sha for c in previous if c.nominated)
    assert None not in shas, 'None in shas'

    for commit in reversed(commits):
        if not commit.nominated and commit.nomination_type is NominationType.FIXES:
            commit.nominated = commit.because_sha in shas

        if commit.nominated:
            shas.add(commit.sha)

    for commit in commits:
        if (commit.nomination_type is NominationType.REVERT and
                commit.because_sha in shas):
            for oldc in reversed(commits):
                if oldc.sha == commit.because_sha:
                    # In this case a commit that hasn't yet been applied is
                    # reverted, we don't want to apply that commit at all
                    oldc.nominated = False
                    oldc.resolution = Resolution.DENOMINATED
                    commit.nominated = False
                    commit.resolution = Resolution.DENOMINATED
                    shas.remove(commit.because_sha)
                    break


async def gather_commits(version: str, previous: typing.List['Commit'],
                         new: typing.List[typing.Tuple[str, str]], cb) -> typing.List['Commit']:
    # We create an array of the final size up front, then we pass that array
    # to the "inner" co-routine, which is turned into a list of tasks and
    # collected by asyncio.gather. We do this to allow the tasks to be
    # asyncrounously gathered, but to also ensure that the commits list remains
    # in order.
    commits = [None] * len(new)
    tasks = []

    async def inner(commit: 'Commit', version: str, commits: typing.List['Commit'],
                    index: int, cb) -> None:
        commits[index] = await resolve_nomination(commit, version)
        cb()

    for i, (sha, desc) in enumerate(new):
        tasks.append(asyncio.ensure_future(
            inner(Commit(sha, desc), version, commits, i, cb)))

    await asyncio.gather(*tasks)
    assert None not in commits

    await resolve_fixes(commits, previous)

    for commit in commits:
        if commit.resolution is Resolution.UNRESOLVED and not commit.nominated:
            commit.resolution = Resolution.NOTNEEDED

    return commits


def load() -> typing.List['Commit']:
    p = pathlib.Path(__file__).parent.parent.parent / '.pick_status.json'
    if not p.exists():
        return []
    with p.open('r') as f:
        raw = json.load(f)
        return [Commit.from_json(c) for c in raw]


def save(commits: typing.Iterable['Commit']) -> None:
    p = pathlib.Path(__file__).parent.parent.parent / '.pick_status.json'
    commits = list(commits)
    with p.open('wt') as f:
        json.dump([c.to_json() for c in commits], f, indent=4)

    asyncio.ensure_future(commit_state(message=f'Update to {commits[0].sha}'))