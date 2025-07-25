## scripts

collection of command-line utilities

### `seq`

This is an implementation of `seq(1)` in pure bash script.
It is intended to be easier to install than binary coreutils.

### `add_leading_zeroes.pl`

#### Usage

    add_leading_zeroes.pl [dir]

Will prompt for confirmation if attached to a terminal.

#### Example

    $ add_leading_zeroes.pl
    2-cool-4-school.mp3   => 02-cool-04-school.mp3
    track-1.mp3           => track-01.mp3
    track-20.mp3          => track-20.mp3
    Make these changes? (y/n) 
