#!/usr/bin/env python3
# coding=utf-8
"""
CutScape: cut the selection (or the whole drawing if nothing is selected)
on a vinyl cutter or plotter, or preview the toolpath on the canvas.
"""
import os
import sys

#: Find the cutscape package next to this script, even through a symlink
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

import inkex  # noqa: E402

from cutscape import document, job, preview, transports  # noqa: E402

CONNECTION = dict(transport='printer', printer='', device='/dev/usb/lp0',
                  port='', baud='9600', flow='xonxoff',
                  file='~/cutscape.plt')


class CutScape(inkex.EffectExtension):

    def add_arguments(self, pars):
        pars.add_argument('--tab')
        pars.add_argument('--action', default='preview',
                          choices=('preview', 'send', 'clear'))
        for name, default in job.DEFAULTS.items():
            kind = inkex.Boolean if isinstance(default, bool) \
                else type(default)
            pars.add_argument('--' + name, type=kind, default=default)
        for name, default in CONNECTION.items():
            pars.add_argument('--' + name, default=default)

    def effect(self):
        o = self.options
        if o.action == 'clear':
            preview.remove(self.svg)
            return
        settings = job.Settings(**{k: getattr(o, k) for k in job.DEFAULTS})
        try:
            plan = self.plan(settings)
            problems = plan.problems()
            if o.action == 'preview':
                preview.draw(self.svg, plan)
                if problems:
                    self.msg('\n'.join(problems))
                return
            if problems:
                raise inkex.AbortExtension('\n'.join(problems))
            data = job.encode(plan, settings)
            where = transports.send(
                data, o.transport, printer=o.printer, device=o.device,
                port=o.port, baud=o.baud, flow=o.flow, file=o.file,
                ack=plan.device['protocol'] == 'gcode')
        except (ValueError, transports.TransportError) as e:
            raise inkex.AbortExtension(str(e))
        x0, y0, x1, y1 = plan.bbox
        self.msg("Sent %.0f x %.0f mm, %.2f m of cuts (%i bytes) to %s." % (
            x1 - x0, y1 - y0, plan.cut_length / 1000, len(data), where))

    def plan(self, settings):
        #: inkex drops ids it can't find; an unmatched selection must fail
        #: rather than cut the whole document
        ids = list(self.options.ids or []) or None
        svg = self.svg
        roots = document.selected_roots(svg, ids)
        texts = document.find_text(svg, roots)
        if texts:
            #: Convert text on a copy; the user's document is untouched
            converted = document.text_to_paths(
                self.document, [t.get('id') for t in texts])
            svg = converted.getroot()
            roots = document.selected_roots(svg, ids)
        polys = document.polylines(svg, roots, settings.tolerance)
        _, page_height = document.page_size(svg)
        return job.plan(polys, page_height, settings)


if __name__ == '__main__':
    CutScape().run()
