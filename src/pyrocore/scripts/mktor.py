# -*- coding: utf-8 -*-
# pylint: disable=
""" Metafile Creator.

    Copyright (c) 2009, 2010, 2011 The PyroScope Project <pyroscope.project@gmail.com>
"""
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
from __future__ import absolute_import

import re
import time
import random

from pyrobase import bencode, fmt
from pyrocore import config
from pyrocore.scripts.base import ScriptBase, ScriptBaseWithConfig
from pyrocore.torrent import formatting
from pyrocore.util import metafile, os, xmlrpc


class MetafileCreator(ScriptBaseWithConfig):
    """
        Create a bittorrent metafile.

        If passed a magnet URI as the only argument, a metafile is created
        in the directory specified via the configuration value 'magnet_watch',
        loadable by rTorrent. Which means you can register 'mktor' as a magnet:
        URL handler in Firefox.
    """

    # argument description for the usage information
    ARGS_HELP = "<dir-or-file> <tracker-url-or-alias>... | <magnet-uri>"

    ENTROPY_BITS = 512


    def add_options(self):
        """ Add program options.
        """
        super(MetafileCreator, self).add_options()

        def human(size):
            'Helper for byte sizes'
            text = fmt.human_size(size)
            return text[:4].lstrip() + ('' if size < 1024 else text[-3])

        self.add_bool_option("-p", "--private",
            help="disallow DHT and PEX")
        self.add_bool_option("--no-date",
            help="leave out creation date")
        self.add_value_option("-o", "--output-filename", "PATH",
            help="optional file name (or target directory) for the metafile")
        self.add_value_option("-r", "--root-name", "NAME",
            help="optional root name (default is basename of the data path)")
        self.add_value_option("-x", "--exclude", "PATTERN [-x ...]",
            action="append", default=[],
            help="exclude files matching a glob pattern from hashing")
        self.add_value_option("--comment", "TEXT",
            help="optional human-readable comment")
        self.add_value_option("-s", "--set", "KEY=VAL [-s ...]",
            action="append", default=[],
            help="set a specific key to the given value; omit the '=' to delete a key")
        self.add_value_option("--chunk-min", "SIZE",
            help="set minimum piece size [%s]" % (human(metafile.Metafile.CHUNK_MIN)))
        self.add_value_option("--chunk-max", "SIZE",
            help="set maximum piece size [%s]" % (human(metafile.Metafile.CHUNK_MAX)))
        self.add_bool_option("--no-cross-seed",
            help="do not automatically add a field to the info dict ensuring unique info hashes")
        self.add_value_option("-X", "--cross-seed", "LABEL",
            help="set additional explicit label for cross-seeding"
                 " (changes info hash, use '@entropy' to randomize it)")
        self.add_bool_option("-H", "--hashed", "--fast-resume",
            help="create second metafile containing libtorrent fast-resume information")
        self.add_bool_option("--load",
            help="load newly created item directly into client")
        self.add_bool_option("--start",
            help="start newly created item directly in the client")
# TODO: Optionally limit disk I/O bandwidth used (incl. a config default!)
# TODO: Set "encoding" correctly
# TODO: Support multi-tracker extension ("announce-list" field)
# TODO: DHT "nodes" field?! [[str IP, int port], ...]
# TODO: Web-seeding http://www.getright.com/seedtorrent.html
#       field 'url-list': ['http://...'] on top-level


    def make_magnet_meta(self, magnet_uri):
        """ Create a magnet-uri torrent.
        """
        import cgi
        import hashlib

        if magnet_uri.startswith("magnet:"):
            magnet_uri = magnet_uri[7:]
        meta = {"magnet-uri": "magnet:" + magnet_uri}
        magnet_params = cgi.parse_qs(magnet_uri.lstrip('?'))

        meta_name = magnet_params.get("xt", [hashlib.sha1(magnet_uri).hexdigest()])[0]
        if "dn" in magnet_params:
            meta_name = "%s-%s" % (magnet_params["dn"][0], meta_name)
        meta_name = re.sub(r"[^-_,a-zA-Z0-9]+", '.', meta_name).strip('.').replace("urn.btih.", "")

        if not config.magnet_watch:
            self.fatal("You MUST set the 'magnet_watch' config option!")
        meta_path = os.path.join(config.magnet_watch, "magnet-%s.torrent" % meta_name)
        self.LOG.debug("Writing magnet-uri metafile %r..." % (meta_path,))

        try:
            bencode.bwrite(meta_path, meta)
        except EnvironmentError as exc:
            self.fatal("Error writing magnet-uri metafile %r (%s)" % (meta_path, exc,))
            raise


    def mainloop(self):
        """ The main loop.
        """
        if len(self.args) == 1 and "=urn:btih:" in self.args[0]:
            # Handle magnet link
            self.make_magnet_meta(self.args[0])
            return

        if not self.args:
            self.parser.print_help()
            self.parser.exit()
        elif len(self.args) < 2:
            self.parser.error("Expected a path and at least one announce URL, got: %s" % (' '.join(self.args),))

        # Create and configure metafile factory
        datapath = self.args[0].rstrip(os.sep)
        metapath = datapath
        if self.options.output_filename:
            metapath = self.options.output_filename
            if os.path.isdir(metapath):
                metapath = os.path.join(metapath, os.path.basename(datapath))
        if not metapath.endswith(".torrent"):
            metapath += ".torrent"
        torrent = metafile.Metafile(metapath)
        torrent.ignore.extend(self.options.exclude)

        def callback(meta):
            "Callback to set label and resume data."
            if self.options.cross_seed:
                if self.options.cross_seed == "@entropy":
                    meta["info"]["entropy"] = format(random.getrandbits(self.ENTROPY_BITS),
                                                     'x').zfill(self.ENTROPY_BITS//4)
                else:
                    meta["info"]["x_cross_seed_label"] = self.options.cross_seed
            if self.options.no_cross_seed:
                del meta["info"]["x_cross_seed"]

            # Set specific keys?
            metafile.assign_fields(meta, self.options.set, self.options.debug)

        # Create and write the metafile(s)
        # TODO: make it work better with multiple trackers (hash only once), also create fast-resume file for each tracker
        meta = torrent.create(datapath, self.args[1:],
            progress=None if self.options.quiet else metafile.console_progress(),
            root_name=self.options.root_name, private=self.options.private, no_date=self.options.no_date,
            comment=self.options.comment, created_by="PyroScope %s" % self.version, callback=callback,
            chunk_min=formatting.parse_sz(self.options.chunk_min),
            chunk_max=formatting.parse_sz(self.options.chunk_max),
        )
        tied_file = metapath

        # Create second metafile with fast-resume?
        if self.options.hashed:
            try:
                metafile.add_fast_resume(meta, datapath)
            except EnvironmentError as exc:
                self.fatal("Error making fast-resume data (%s)" % (exc,))
                raise

            hashed_path = re.sub(r"\.torrent$", "", metapath) + "-resume.torrent"
            self.LOG.info("Writing fast-resume metafile %r..." % (hashed_path,))
            try:
                bencode.bwrite(hashed_path, meta)
                tied_file = hashed_path
            except EnvironmentError as exc:
                self.fatal("Error writing fast-resume metafile %r (%s)" % (hashed_path, exc,))
                raise

        # Load into client on demand
        if self.options.load or self.options.start:
            proxy = config.engine.open()
            info_hash = metafile.info_hash(meta)
            try:
                item_name = proxy.d.name(info_hash, fail_silently=True)
            except xmlrpc.HashNotFound:
                load_item = proxy.load.start_verbose if self.options.start else proxy.load.verbose
                load_item(xmlrpc.NOHASH, os.path.abspath(tied_file))
                time.sleep(.05) # let things settle
                try:
                    item_name = proxy.d.name(info_hash, fail_silently=True)
                    self.LOG.info("OK: Item #%s %s client.",
                                  info_hash, 'started in' if self.options.start else 'loaded into')
                except xmlrpc.HashNotFound as exc:
                    self.fatal("Error while loading item #%s into client: %s" % (info_hash, exc,))
            else:
                self.LOG.warning("Item #%s already exists in client, --load/--start is ignored!", info_hash)


def run(): #pragma: no cover
    """ The entry point.
    """
    ScriptBase.setup()
    MetafileCreator().run()


if __name__ == "__main__":
    run()
