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
    align, align_window, cutter, devices, document, job, marks, notify,
    preview, transports)
from cutscape.i18n import _  # noqa: E402

CONNECTION = dict(transport=cutter.ADDED, printer='', device='/dev/usb/lp0',
                  port='', baud='9600', flow='xonxoff',
                  file='~/cutscape.plt')

#: Extensions > CutScape > Add Cutter
ADD = dict(model=devices.CUSTOM, address='')

#: mm; also checks the scale
TEST_SQUARE = 50.0

#: What Cut's "Apply will" offers
CUT_ACTIONS = ('preview', 'send', 'test', 'align')


class CutScape(inkex.EffectExtension):

    def add_arguments(self, pars):
        pars.add_argument('--tab')
        #: Cut's "Apply will". Not choices=: Inkscape passes a saved choice
        #: the dialog no longer offers (0.2.0's "find", say) as it is, while
        #: showing Preview as chosen
        pars.add_argument('--action', default='preview')
        #: The Add Cutter, Remove Cutter, Remove Preview and Add Crop Marks
        #: menu entries run this script too, with a fixed, hidden --entry and
        #: only their own parameters. Inkscape doesn't save hidden parameters,
        #: so these can't go stale.
        pars.add_argument('--entry',
                          choices=('add', 'remove', 'clear', 'marks'))
        for name, default in job.DEFAULTS.items():
            kind = inkex.Boolean if isinstance(default, bool) \
                else type(default)
            pars.add_argument('--' + name, type=kind, default=default)
        for name, default in list(CONNECTION.items()) + list(ADD.items()):
            pars.add_argument('--' + name, default=default)

    def effect(self):
        o = self.options
        if o.entry:
            o.action = o.entry
        elif o.action not in CUT_ACTIONS:
            o.action = 'preview'
        if o.action == 'clear':
            preview.remove(self.svg)
            return
        if o.action == 'marks':
            self.add_marks()
            return
        if o.action in ('add', 'remove'):
            try:
                if o.action == 'add':
                    text, ready = cutter.add(o.model, o.address)
                else:
                    text, ready = cutter.remove(), True
            except (ValueError, cutter.CutterError) as e:
                #: "No cutter found" is an answer, not a crash
                if not notify.show(str(e), kind=notify.WARNING):
                    raise inkex.AbortExtension(str(e))
                return
            self.say(text, notify.INFO if ready else notify.WARNING)
            return
        try:
            cutter.use(o, sending=o.action != 'preview')
        except (ValueError, cutter.CutterError) as e:
            raise inkex.AbortExtension(str(e))
        settings = job.Settings(**{k: getattr(o, k) for k in job.DEFAULTS})
        try:
            if o.action == 'align':
                self.cut_on_sheet(settings)
                return
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
            where = self.send(data, ack=plan.device['protocol'] == 'gcode')
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

    def send(self, data, **options):
        o = self.options
        return transports.send(
            data, o.transport, printer=o.printer, device=o.device, port=o.port,
            baud=o.baud, flow=o.flow, file=o.file, **options)

    def add_marks(self):
        try:
            fits = marks.add(self.svg, list(self.options.ids or []) or None)
        except ValueError as e:
            raise inkex.AbortExtension(str(e))
        lines = [_("Added four crop marks, and moved the lines to cut to a "
                   "hidden layer so they don't print.")]
        if not fits:
            lines.append(_("The marks don't all fit on the page, leaving room "
                           "for the printer's margin. Make the page bigger or "
                           "the design smaller, and add them again."))
        lines.append(_("Next, print it at 100%, with no \"fit to page\". Put "
                       "the sheet in the cutter and set the cutter's origin "
                       "below and to the left of mark 1. Then open Extensions "
                       "> CutScape > Cut, choose Line up with printed crop "
                       "marks and cut, and click Apply."))
        self.say('\n\n'.join(lines),
                 notify.INFO if fits else notify.WARNING)

    def cut_on_sheet(self, settings):
        """ Line up with the printed sheet in a window of its own, then cut
        what Add Crop Marks set aside
        """
        device = settings.device()
        align.check(device, self.options.transport)
        page_marks = document.crop_marks(self.svg)
        polys = self.polylines(settings, document.cut_lines)
        head = align.Head(device, settings, lambda data: self.send(
            data, timeout=align.MOVE_TIMEOUT))
        session = align.Session(
            head, page_marks,
            align.base(device, document.page_size(self.svg)[1]),
            cutter.setting('laser'), cutter.setting('arrow_turn', 0))

        def plan():
            return job.plan_on_sheet(polys, session.to_head(), settings)
        if not align_window.run(session, lambda: plan().problems()):
            return
        cut = plan()
        where = self.send(job.encode(cut, settings))
        self.say(_("Sent {metres:.2f} m of cuts, lined up with the printed "
                   "sheet, to {where}.").format(
            metres=cut.cut_length / 1000, where=where))

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
        polys = self.polylines(
            settings, lambda svg: document.selected_roots(svg, ids))
        page_height = document.page_size(self.svg)[1]
        return job.plan(polys, page_height, settings)

    def polylines(self, settings, roots_of):
        """ Flatten what roots_of finds in a document to page polylines """
        svg = self.svg
        roots = roots_of(svg)
        texts = document.find_text(svg, roots)
        if texts:
            #: Convert text on a copy; the user's document is untouched
            converted = document.text_to_paths(
                self.document, [t.get('id') for t in texts])
            svg = converted.getroot()
            roots = roots_of(svg)
        return document.polylines(svg, roots, settings.tolerance)


if __name__ == '__main__':
    CutScape().run()
