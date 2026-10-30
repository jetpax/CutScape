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

from cutscape import (  # noqa: E402
    document, job, notify, preview, printers, transports)
from cutscape.i18n import _  # noqa: E402

CONNECTION = dict(transport='printer', printer='', device='/dev/usb/lp0',
                  port='', baud='9600', flow='xonxoff',
                  file='~/cutscape.plt', printer_task='create',
                  printer_uri='')

#: mm; also checks the scale
TEST_SQUARE = 50.0


class CutScape(inkex.EffectExtension):

    def add_arguments(self, pars):
        pars.add_argument('--tab')
        pars.add_argument('--action', default='preview',
                          choices=('preview', 'send', 'test', 'find',
                                   'printers', 'clear'))
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
        if o.action == 'find':
            self.say(transports.find_cutters(), report=True)
            return
        if o.action == 'printers':
            try:
                self.say(printers.manage(o.printer_task, o.printer.strip(),
                                         o.printer_uri.strip()))
            except printers.PrinterError as e:
                raise inkex.AbortExtension(str(e))
            return
        settings = job.Settings(**{k: getattr(o, k) for k in job.DEFAULTS})
        try:
            if o.action == 'test':
                plan = self.test_plan(settings)
            else:
                plan = self.plan(settings)
            problems = plan.problems()
            if o.action == 'preview':
                preview.draw(self.svg, plan)
                if problems:
                    self.say('\n'.join(problems), notify.WARNING)
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
        if o.action == 'test':
            self.say(_("Sent a {size:.0f} mm test square to {where}. Measure "
                       "it: if it isn't {size:.0f} mm, the cutter's steps "
                       "per mm are wrong.").format(size=TEST_SQUARE,
                                                   where=where))
            return
        x0, y0, x1, y1 = plan.bbox
        self.say(_("Sent {width:.0f} × {height:.0f} mm, {metres:.2f} m of "
                   "cuts ({size} bytes) to {where}.").format(
            width=x1 - x0, height=y1 - y0, metres=plan.cut_length / 1000,
            size=len(data), where=where))

    def say(self, text, kind=notify.INFO, report=False):
        """ Tell the user something that isn't an error, in CutScape's own
        window when possible (see notify)
        """
        if not notify.show(text, kind=kind, report=report):
            self.msg(text)

    @staticmethod
    def test_plan(settings):
        """ A square at the origin, with the dialog's device, connection
        and cut settings, ignoring the drawing and the material layout
        """
        for name in ('mirror', 'rotate', 'weedline', 'prefeed'):
            setattr(settings, name, False)
        settings.copies, settings.passes = 1, 1
        settings.auto_align = True
        settings.margin_x = settings.margin_y = 0.0
        settings.feed = 0.0
        size = TEST_SQUARE
        square = [(0, 0), (size, 0), (size, size), (0, size), (0, 0)]
        return job.plan([square], size, settings)

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
        page_height = document.page_size(svg)[1]
        return job.plan(polys, page_height, settings)


if __name__ == '__main__':
    CutScape().run()
