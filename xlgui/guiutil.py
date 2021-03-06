# Copyright (C) 2008-2010 Adam Olsen
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2, or (at your option)
# any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
#
#
# The developers of the Exaile media player hereby grant permission
# for non-GPL compatible GStreamer and Exaile plugins to be used and
# distributed together with GStreamer and Exaile. This permission is
# above and beyond the permissions granted by the GPL license by which
# Exaile is covered. If you modify this code, you may extend this
# exception to your version of the code, but you are not obligated to
# do so. If you do not wish to do so, delete this exception statement
# from your version.

import logging
import os.path

from gi.repository import Gio
from gi.repository import Gdk
from gi.repository import GdkPixbuf
from gi.repository import GLib
from gi.repository import Gtk
from gi.repository import Pango

from xl import settings, xdg
from xlgui import icons

# Import from external namespace
from xl.externals.gi_composites import GtkTemplate as _GtkTemplate

logger = logging.getLogger(__name__)


class GtkTemplate(_GtkTemplate):
    '''
        Use this class decorator in conjunction with :class:`.GtkCallback`
        and :class:`GtkChild` to construct widgets from a GtkBuilder UI
        file.

        This is an exaile-specific wrapper around the :class:`.GtkTemplate`
        object to allow loading the UI template file in an Exaile-specific
        way.

        :param *path: Path components to specify UI file
        :param relto: If keyword arg 'relto' is specified, path will be
                      relative to this. Otherwise, it will be relative to
                      the Exaile data directory

        .. versionadded:: 3.5.0
    '''

    def __init__(self, *path, **kwargs):
        super(GtkTemplate, self).__init__(ui=ui_path(*path, **kwargs))


def ui_path(*path, **kwargs):
    '''
        Returns absolute path to a UI file. Each arg will be concatenated
        to construct the final path.

        :param relto: If keyword arg 'relto' is specified, path will be
                      relative to this. Otherwise, it will be relative to
                      the Exaile data directory

        .. versionadded:: 3.5.0
    '''

    relto = kwargs.pop('relto', None)
    if len(kwargs):
        raise ValueError("Only 'relto' is allowed as a keyword argument")

    if relto is None:
        return xdg.get_data_path(*path)
    else:
        return os.path.abspath(os.path.join(os.path.dirname(relto), *path))


def get_workarea_size():
    """
        Returns the width and height of the work area
    """
    d = get_workarea_dimensions()
    return (d.width, d.height)


def get_workarea_dimensions(window=None):
    """
        Returns the x-offset, y-offset, width and height of the work area
        for a given window or for the default screen if no window is given.
        Falls back to the screen dimensions if not available.

        :param window: class: `Gtk.Window`, optional

        :returns: :class:`CairoRectangleInt`
    """
    if window is None:
        screen = Gdk.Screen.get_default()
        default_monitor = screen.get_primary_monitor()
        return screen.get_monitor_workarea(default_monitor)
    elif Gtk.get_major_version() > 3 or \
            Gtk.get_major_version() == 3 and Gtk.get_minor_version() >= 22:
        # Gdk.Monitor was introduced in Gtk+ 3.22
        display = window.get_window().get_display()
        work_area = display.get_monitor_at_window(window.get_window()).get_workarea()
    else:
        screen = window.get_screen()
        monitor_nr = screen.get_monitor_at_window(window.get_window())
        work_area = screen.get_monitor_workarea(monitor_nr)
    return work_area


def gtk_widget_replace(widget, replacement):
    """
        Replaces one widget with another and places it exactly at the
        original position, keeping child properties

        :param widget: The original widget
        :type widget: :class:`Gtk.Widget`
        :param replacement: The new widget
        :type widget: :class:`Gtk.Widget`

        :returns: replacement widget if successful
    """
    parent = widget.get_parent()

    if parent is None:
        logger.error("widget doesn't have a parent.")
        return

    props = {}
    for pspec in parent.list_child_properties():
        props[pspec.name] = parent.child_get_property(widget, pspec.name)

    parent.remove(widget)
    parent.add(replacement)

    for name, value in props.items():
        parent.child_set_property(replacement, name, value)
    return replacement


class ScalableImageWidget(Gtk.Image):
    """
        Custom resizeable image widget
    """

    def __init__(self):
        """
            Initializes the image
        """
        Gtk.Image.__init__(self)

    def set_image_size(self, width, height):
        """
            Scales the size of the image
        """
        self.size = (width, height)
        self.set_size_request(width, height)

    def set_image(self, location, fill=False):
        """
            Sets the image from a location

            :param location: the location to load the image from
            :type location: string
            :param fill: True to expand the image, False to keep its ratio
            :type fill: boolean
        """
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(Gio.File.new_for_uri(location).get_path())
        self.set_image_pixbuf(pixbuf, fill)

    def set_image_data(self, data, fill=False):
        """
            Sets the image from binary data

            :param data: the binary data
            :type data: string
            :param fill: True to expand the image, False to keep its ratio
            :type fill: boolean
        """
        if not data:
            return
        pixbuf = icons.MANAGER.pixbuf_from_data(data)
        self.set_image_pixbuf(pixbuf, fill)

    def set_image_pixbuf(self, pixbuf, fill=False):
        """
            Sets the image from a pixbuf

            :param data: the pixbuf
            :type data: :class:`GdkPixbuf.Pixbuf`
            :param fill: True to expand the image, False to keep its ratio
            :type fill: boolean
        """
        width, height = self.size
        if not fill:
            origw = float(pixbuf.get_width())
            origh = float(pixbuf.get_height())
            scale = min(width / origw, height / origh)
            width = int(origw * scale)
            height = int(origh * scale)
        self.width = width
        self.height = height
        scaled = pixbuf.scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR)
        self.set_from_pixbuf(scaled)

        scaled = pixbuf = None


class SearchEntry(object):
    """
        A Gtk.Entry that emits the "activated" signal when something has
        changed after the specified timeout
    """

    def __init__(self, entry=None, timeout=500):
        """
            Initializes the entry
        """
        self.entry = entry
        self.timeout = timeout
        self.change_id = None

        if entry is None:
            self.entry = entry = Gtk.Entry()

        self._last_text = entry.get_text()

        entry.connect('changed', self.on_entry_changed)
        entry.connect('icon-press', self.on_entry_icon_press)
        entry.connect('activate', self.on_entry_activated)

    def on_entry_changed(self, entry):
        """
            Called when the entry changes
        """
        empty_search = (entry.get_text() == '')
        entry.props.secondary_icon_sensitive = not empty_search

        if self.change_id:
            GLib.source_remove(self.change_id)
        self.change_id = GLib.timeout_add(self.timeout,
                                          self.entry_activate)

    def on_entry_icon_press(self, entry, icon_pos, event):
        """
            Clears the entry
        """
        self.entry.set_text('')
        self.entry_activate()

    def on_entry_activated(self, entry):
        self._last_text = entry.get_text()

    def entry_activate(self, *e):
        """
            Emit the activate signal
        """
        self.change_id = None
        if self.entry.get_text() != self._last_text:
            self.entry.activate()

    def __getattr__(self, attr):
        """
            Tries to pass attribute requests
            to the internal entry item
        """
        return getattr(self.entry, attr)


class ModifierType:
    '''
        Common Gdk.ModifierType combinations that work in a cross platform way
    '''

    #
    # Missing from Gdk.ModifierType
    #

    #: Apple/cmd on OSX, CTRL elsewhere (taken from QuodLibet)
    PRIMARY_MASK = Gtk.accelerator_parse("<Primary>")[1]

    #: primary + shift
    PRIMARY_SHIFT_MASK = PRIMARY_MASK | Gdk.ModifierType.SHIFT_MASK

    #
    # The rest are for completeness..
    #

    #: shift
    SHIFT_MASK = Gdk.ModifierType.SHIFT_MASK


def position_menu(menu, *args):
    '''
        A function that will position a menu near a particular widget. This
        should be specified as the third argument to menu.popup(), with the
        user data being the widget.

            menu.popup_menu(None, None, guiutil.position_menu, widget,
                            0, 0)
    '''
    # Prior to GTK+ 3.16, args contains only our user data.
    # Since 3.16, we get (orig_x, orig_y, data).
    # See https://git.gnome.org/browse/gtk+/commit/?id=8463d0ee62b4b22fa
    widget = args[-1]
    window = widget.get_window()
    _, window_x, window_y = window.get_origin()
    widget_allocation = widget.get_allocation()
    menu_allocation = menu.get_allocation()
    position = (
        window_x + widget_allocation.x + 1,
        window_y + widget_allocation.y - menu_allocation.height - 1
    )

    return (position[0], position[1], True)


def finish(repeat=True):
    """
        Waits for current pending gtk events to finish
    """
    while Gtk.events_pending():
        Gtk.main_iteration()
        if not repeat:
            break


def initialize_from_xml(this, other=None):
    '''
        DEPRECATED. Use GtkComposite, GtkCallback, and GtkChild instead

        Initializes the widgets and signals from a GtkBuilder XML file. Looks
        for the following attributes in the instance you pass:

        ui_filename = builder filename -- either an absolute path, or a tuple
                      with the path relative to the xdg data directory.
        ui_widgets = [list of widget names]
        ui_signals = [list of function names to connect to a signal]

        For each widget in ui_widgets, it will be retrieved from the builder
        object and set as an attribute on the object you pass in.

        other is a list of widgets to also initialize with the same file

        Returns the builder object when done
    '''
    builder = Gtk.Builder()

    if isinstance(this.ui_filename, basestring) and os.path.exists(this.ui_filename):
        builder.add_from_file(this.ui_filename)
    else:
        builder.add_from_file(xdg.get_data_path(*this.ui_filename))

    objects = [this]
    if other is not None:
        objects.extend(other)

    for obj in objects:
        if hasattr(obj, 'ui_widgets') and obj.ui_widgets is not None:
            for widget_name in obj.ui_widgets:
                widget = builder.get_object(widget_name)
                if widget is None:
                    raise RuntimeError("Widget '%s' is not present in '%s'" % (widget_name, this.ui_filename))
                setattr(obj, widget_name, widget)

    signals = None

    for obj in objects:
        if hasattr(obj, 'ui_signals') and obj.ui_signals is not None:
            if signals is None:
                signals = {}
            for signal_name in obj.ui_signals:
                if not hasattr(obj, signal_name):
                    raise RuntimeError("Function '%s' is not present in '%s'" % (signal_name, obj))
                signals[signal_name] = getattr(obj, signal_name)

    if signals is not None:
        missing = builder.connect_signals(signals)
        if missing is not None:
            err = 'The following signals were found in %s but have no assigned handler: %s' % (this.ui_filename, str(missing))
            raise RuntimeError(err)

    return builder


def persist_selection(widget, key_col, setting_name):
    '''
        Given a widget that is using a Gtk.ListStore, it will restore the
        selected index given the contents of a setting. When the widget
        changes, it will save the choice.

        Call this on the widget after you have loaded data
        into the widget.

        :param widget:         Gtk.ComboBox or Gtk.TreeView
        :param col:            Integer column with unique key
        :param setting_name:   Setting to save key to/from
    '''

    model = widget.get_model()

    key = settings.get_option(setting_name)
    if key is not None:
        for i in xrange(0, len(model)):
            if model[i][key_col] == key:
                if hasattr(widget, 'set_active'):
                    widget.set_active(i)
                else:
                    widget.set_cursor((i,))
                break

    if hasattr(widget, 'set_active'):

        def _on_changed(widget):
            active = widget.get_model()[widget.get_active()][key_col]
            settings.set_option(setting_name, active)

        widget.connect('changed', _on_changed)

    else:

        def _on_changed(widget):
            model, i = widget.get_selected()
            active = model[i][key_col]
            settings.set_option(setting_name, active)

        widget.get_selection().connect('changed', _on_changed)


def platform_is_wayland():
    """
        This function checks whether Exaile has been started on a Wayland display.

        This function has been tested on both GNOME and Weston Wayland compositors

        :returns: `True` if the display used by Exaile is using a Wayland compositor,
                    ` False` otherwise.
    """
    display_name = Gdk.Display.get_default().get_name().lower()
    return 'wayland' in display_name


def platform_is_x11():
    """
        This function checks whether Exaile has been started on a X11 Gdk backend.

        :returns: `True` if the display used by Exaile is realized on a X11 server,
                    ` False` otherwise.
    """
    display_name = Gdk.Display.get_default().get_name().lower()
    return 'x11' in display_name


def css_from_rgba(rgba):
    """
        Convert a Gdk.RGBA to a CSS color string
    """
    color_css_str = "rgba(%s, %s, %s, %s)" % (
        str(int(rgba.red * 255)),
        str(int(rgba.green * 255)),
        str(int(rgba.blue * 255)),
        str(rgba.alpha),
    )
    return color_css_str


def css_from_rgba_without_alpha(rgba):
    """
        Convert a Gdk.RGBA to a CSS color string removing the alpha channel
    """
    color_css_str = "rgb(%s, %s, %s)" % (
        str(int(rgba.red * 255)),
        str(int(rgba.green * 255)),
        str(int(rgba.blue * 255)),
    )
    return color_css_str


def css_from_pango_font_description(pango_font_str):
    """
        Convert a Pango.FontDescription string to a CSS font string
    """
    if pango_font_str is None:
        return ""

    new_font = Pango.FontDescription.from_string(pango_font_str)

    # Gtk+ CSS and Pango are compatible except for the prefix and case
    style_name = new_font.get_style().value_name
    style = style_name.split('_', 2)[2].lower()

    # Gtk+ CSS only allows 100 | 200 | 300 | 400 | 500 | 600 | 700 | 800 | 900
    # Pango allows everything from 100 through 1000, with several named values
    # at least the numbers mean the same
    # Bug in PyGObject: This does not work
    weight_int = int(new_font.get_weight())
    css_weight_int = ((weight_int + 50) // 100) * 100
    if css_weight_int > 900:
        css_weight_int = 900
    weight = str(css_weight_int)

    # Gtk+ CSS and Pango are compatible except for
    # * the prefix
    # * letter case
    # * the fact that Pango uses an underscore whereas Gtk+ CSS uses a dash
    stretch_name = new_font.get_stretch().value_name
    stretch = stretch_name.split('_', 2)[2].lower().replace('_', '-')

    # Gtk+ CSS and Pango are compatible except for
    # * the prefix
    # * letter case
    # * the fact that Pango uses an underscore whereas Gtk+ CSS uses a dash
    variant_name = new_font.get_variant().value_name
    variant = variant_name.split('_', 2)[2].lower().replace('_', '-')

    # Pango multiplies its font by Pango.SCALE
    size = str(new_font.get_size() / Pango.SCALE) + "pt"

    # See "GTK+ CSS" documentation page for the syntax

    # According to https://www.w3schools.com/cssref/pr_font_font-family.asp
    # "If a font name contains white-space, it must be quoted"
    font_css_str = 'font: %s %s %s %s %s "%s"' % (
        style, variant, weight, stretch, size, new_font.get_family(),
    )
    return font_css_str

# vim: et sts=4 sw=4
