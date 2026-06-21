# This file is part of beets.
# Copyright 2016
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.

"""beets plugin collection.

This package contains all built-in beets plugins. Plugins are discovered
by name: each plugin module defines a class that inherits from BeetsPlugin.

To enable a plugin, add its name to the ``plugins`` list in beets config.
"""

__all__ = []
