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
# Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.
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

import datetime
import logging
import os
import re
import threading

import cairo
import glib
import gobject
import pygst
pygst.require('0.10')
import gst
import pygtk
pygtk.require('2.0')
import gtk
import pango

from xl import (
    common,
    event,
    formatter,
    providers,
    settings,
    trax,
    xdg
)
from xl.nls import gettext as _
import xl.playlist
from xlgui import (
    commondialogs,
    cover,
    guiutil,
    menu,
    playlist,
    tray
)

logger = logging.getLogger(__name__)

class PlaybackProgressBar(object):
    def __init__(self, bar, player):
        self.bar = bar
        self.player = player
        self.timer_id = None
        self.seeking = False
        self.formatter = formatter.ProgressTextFormatter()

        self.bar.set_text(_('Not Playing'))
        self.bar.connect('button-press-event', self.seek_begin)
        self.bar.connect('button-release-event', self.seek_end)
        self.bar.connect('motion-notify-event', self.seek_motion_notify)

        event.add_callback(self.playback_start,
            'playback_player_start', player)
        event.add_callback(self.playback_toggle_pause,
            'playback_toggle_pause', player)
        event.add_callback(self.playback_end,
            'playback_player_end', player)

    def destroy(self):
        event.remove_callback(self.playback_start,
                'playback_player_start', self.player)
        event.remove_callback(self.playback_end,
                'playback_player_end', self.player)

    def seek_begin(self, *e):
        self.seeking = True

    def seek_end(self, widget, event):
        mouse_x, mouse_y = event.get_coords()
        progress_loc = self.bar.get_allocation()

        value = mouse_x / progress_loc.width
        if value < 0: value = 0
        if value > 1: value = 1

        tr = self.player.current
        if not tr or not (tr.is_local() or \
                tr.get_tag_raw('__length')): return
        length = tr.get_tag_raw('__length')

        seconds = float(value * length)
        self.player.seek(seconds)
        self.seeking = False
        self.bar.set_fraction(value)
        self.bar.set_text(self.formatter.format(seconds, length))
#        self.emit('seek', seconds)

    def seek_motion_notify(self, widget, event):
        tr = self.player.current
        if not tr or not(tr.is_local() or \
                tr.get_tag_raw('__length')): return

        mouse_x, mouse_y = event.get_coords()
        progress_loc = self.bar.get_allocation()

        value = mouse_x / progress_loc.width

        if value < 0: value = 0
        if value > 1: value = 1

        self.bar.set_fraction(value)
        length = tr.get_tag_raw('__length')
        seconds = float(value * length)
        remaining_seconds = length - seconds
        self.bar.set_text(self.formatter.format(seconds, length))

    def playback_start(self, type, player, object):
        if self.timer_id:
            glib.source_remove(self.timer_id)
            self.timer_id = None
        self.__add_timer_update()

    def playback_toggle_pause(self, type, player, object):
        if self.timer_id:
            glib.source_remove(self.timer_id)
            self.timer_id = None
        if not player.is_paused():
            self.__add_timer_update()

    def __add_timer_update(self):
        freq = settings.get_option("gui/progress_update_millisecs", 1000)
        if freq % 1000 == 0:
            self.timer_id = glib.timeout_add_seconds(freq/1000, self.timer_update)
        else:
            self.timer_id = glib.timeout_add(freq, self.timer_update)

    def playback_end(self, type, player, object):
        if self.timer_id: glib.source_remove(self.timer_id)
        self.timer_id = None
        self.bar.set_text(_('Not Playing'))
        self.bar.set_fraction(0)

    def timer_update(self, *e):
        tr = self.player.current
        if not tr: return
        if self.seeking: return True

        if not tr.is_local() and not tr.get_tag_raw('__length'):
            self.bar.set_fraction(0)
            self.bar.set_text(_('Streaming...'))
            return True

        self.bar.set_fraction(self.player.get_progress())

        seconds = self.player.get_time()
        length = tr.get_tag_raw('__length')
        self.bar.set_text(self.formatter.format(seconds, length))

        return True


# Reduce the notebook tabs' close button padding size.
gtk.rc_parse_string("""
    style "thinWidget" {
        xthickness = 0
        ythickness = 0
    }
    widget "*.tabCloseButton" style "thinWidget"
    """)
class NotebookTab(gtk.EventBox):
    """
        A notebook tab, complete with a close button
    """
    def __init__(self, main, notebook, title, page):
        """
            Initializes the tab
        """
        gtk.EventBox.__init__(self)
        self.set_visible_window(False)

        self.main = main
        self.nb = notebook
        self.page = page
        self.already_needs_save = self.page.playlist.get_is_custom() and self.page.get_needs_save()

        self.connect('button_press_event', self.on_button_press)
        self.page.connect('playlist-content-changed', lambda widget, dirty:
                    self.on_playlist_content_change(dirty))
        self.page.connect('customness-changed', lambda widget, custom:
                    self.on_customness_change(custom))
        event.add_callback(self.on_playlist_removed, 'playlist_removed')

        self.hbox = hbox = gtk.HBox(False, 2)
        self.add(hbox)

        if self.already_needs_save and self.page.playlist.get_is_custom():
            self.label = gtk.Label("*" + title)
        else:
            self.label = gtk.Label(title)
        self.label.set_max_width_chars(20)
        self.label.set_ellipsize(pango.ELLIPSIZE_END)
        self.label.set_tooltip_text(self.label.get_text())
        hbox.pack_start(self.label, False, False)

        self.menu = menu.PlaylistTabMenu(self, self.page.playlist.get_is_custom())

        self.button = btn = gtk.Button()
        btn.set_name('tabCloseButton')
        btn.set_relief(gtk.RELIEF_NONE)
        btn.set_focus_on_click(False)
        btn.set_tooltip_text(_("Close tab"))
        btn.connect('clicked', self.do_close)
        btn.connect('button_press_event', self.on_button_press)
        image = gtk.Image()
        image.set_from_stock(gtk.STOCK_CLOSE, gtk.ICON_SIZE_MENU)
        btn.add(image)
        hbox.pack_end(btn, False, False)

        self.show_all()

    def get_title(self):
        return unicode(self.label.get_text(), 'utf-8')
    def set_title(self, title):
        self.label.set_text(title)
        self.label.set_tooltip_text(self.label.get_text())
    title = property(get_title, set_title)

    def on_customness_change(self, custom):
        self.menu.destroy()
        self.menu = None
        self.menu = menu.PlaylistTabMenu(self, custom)

    def on_playlist_removed(self, type, object, name):
        if name == self.page.playlist.name and self.page.playlist.get_is_custom():
            self.page.playlist.set_needs_save(False)
            self.on_playlist_content_change(False)
            self.page.playlist.set_is_custom(False)
            self.on_customness_change(False)

    def on_playlist_content_change(self, dirty):
        if self.page.playlist.get_is_custom():
            if dirty and not self.already_needs_save:
                self.already_needs_save = True
                self.label.set_text('*' + self.label.get_text())
            elif not dirty and self.already_needs_save:
                self.already_needs_save = False
                if self.label.get_text()[0] == '*':
                    self.label.set_text(self.label.get_text()[1:])

    def on_button_press(self, widget, event):
        """
            Called when the user clicks on the tab
        """
        if event.button == 3:
            self.menu.popup(None, None, None, event.button, event.time)
            return True
        elif event.button == 2:
            self.do_close()
            return True
        elif event.button == 1 and event.type == gtk.gdk._2BUTTON_PRESS:
            self.do_rename()
            return True # stop the event propagating

    def do_new_playlist(self, *args):
        self.main.add_playlist()

    def do_rename(self, *args):
        dialog = commondialogs.TextEntryDialog(
            _("New playlist title:"), _("Rename Playlist"),
            self.title, self.main.window)
        response = dialog.run()
        if response == gtk.RESPONSE_OK:
            self.title = dialog.get_value()
            self.page.playlist.set_name(self.title)

    def do_save_custom(self, *args):
        dialog = commondialogs.TextEntryDialog(
            _("Custom playlist name:"), _("Save as..."),
            self.title, self.main.window, okbutton=gtk.STOCK_SAVE)
        response = dialog.run()
        if response == gtk.RESPONSE_OK:
            self.title = dialog.get_value()
            pl = self.main.get_selected_playlist()
            pl.set_name(self.title)
            pl.playlist.set_name(self.title)
            self.main.controller.panels['playlists'].add_new_playlist(pl.playlist.get_tracks(), self.title)
            pl.playlist.set_is_custom(True)
            pl.emit('customness-changed', True)
            pl.set_needs_save(False)
            event.log_event('custom_playlist_saved', self, pl.playlist)

    def do_save_changes_to_custom(self, *args):
        pl = self.main.get_selected_playlist()
        pl.set_needs_save(False)
        self.main.playlist_manager.save_playlist(pl.playlist, overwrite = True)
        event.log_event('custom_playlist_saved', self, pl.playlist)

    def do_close(self, *args):
        """
            Called when the user clicks the close button on the tab
        """
        if self.page.on_closing():
            if self.main.queue.current_playlist == self.page.playlist:
                self.main.queue.set_current_playlist(None)
            num = self.nb.page_num(self.page)
            self.nb.remove_page(num)

    def do_clear(self, *args):
        """
            Clears the current playlist tab
        """
        playlist = self.main.get_selected_playlist()
        if not playlist: return
        playlist.playlist.clear()

class MainWindow(gobject.GObject):
    """
        Main Exaile Window
    """
    __gsignals__ = {'main-visible-toggle': (gobject.SIGNAL_RUN_LAST, bool, ())}
    _mainwindow = None
    def __init__(self, controller, builder, collection,
        player, queue, covers):
        """
            Initializes the main window

            @param controller: the main gui controller
        """
        gobject.GObject.__init__(self)

        self.controller = controller
        self.covers = covers
        self.collection =  collection
        self.player = player
        self.playlist_manager = controller.exaile.playlists
        self.queue = queue
        self.current_page = -1
        self._fullscreen = False
        self.resuming = False

        self.builder = builder
        self.window = self.builder.get_object('ExaileWindow')
        self.window.set_title('Exaile')

        if settings.get_option('gui/use_alpha', False):
            screen = self.window.get_screen()
            colormap = screen.get_rgba_colormap()

            if colormap is not None:
                self.window.set_app_paintable(True)
                self.window.set_colormap(colormap)

                self.window.connect('expose-event', self.on_expose_event)
                self.window.connect('screen-changed', self.on_screen_changed)

        self.playlist_notebook = self.builder.get_object('playlist_notebook')
        self.playlist_notebook.remove_page(0)
        self.playlist_notebook.set_show_tabs(settings.get_option('gui/show_tabbar', True))
        map = {
            'left': gtk.POS_LEFT,
            'right': gtk.POS_RIGHT,
            'top': gtk.POS_TOP,
            'bottom': gtk.POS_BOTTOM
        }
        self.playlist_notebook.set_tab_pos(map.get(
            settings.get_option('gui/tab_placement', 'top')))
        self.splitter = self.builder.get_object('splitter')
        
        self.playlist_utilities_bar = self.builder.get_object('playlist_utilities_bar')
        playlist_utilities_bar_visible = settings.get_option(
            'gui/playlist_utilities_bar_visible', True)
        self.builder.get_object('playlist_utilities_bar_visible').set_active(
            playlist_utilities_bar_visible)
        self.playlist_utilities_bar.set_property('visible', playlist_utilities_bar_visible)
        self.playlist_utilities_bar.set_sensitive(playlist_utilities_bar_visible)
        self.playlist_utilities_bar.set_no_show_all(not playlist_utilities_bar_visible)

        self._setup_position()
        self._setup_widgets()
        self._setup_hotkeys()
        logger.info("Connecting main window events...")
        self._connect_events()
        from xlgui import osd
        self.osd = osd.OSDWindow(self.player)
        self.tab_manager = xl.playlist.PlaylistManager(
            'saved_tabs')
        self.load_saved_tabs()
        MainWindow._mainwindow = self

    def load_saved_tabs(self):
        """
            Loads the saved tabs
        """
        if not settings.get_option('playlist/open_last', False):
            self.add_playlist()
            return
        names = self.tab_manager.list_playlists()
        if not names:
            self.add_playlist()
            return

        count = -1
        count2 = 0
        names.sort()
        # holds the order#'s of the already added tabs
        added_tabs = {}
        name_re = re.compile(
                r'^order(?P<tab>\d+)\.(?P<tag>[^.]*)\.(?P<name>.*)$')
        for i, name in enumerate(names):
            match = name_re.match(name)
            if not match or not match.group('tab') or not match.group('name'):
                logger.error("%s did not match valid playlist file"
                        % repr(name))
                continue

            logger.debug("Adding playlist %d: %s" % (i, name))
            logger.debug("Tab:%s; Tag:%s; Name:%s" % (match.group('tab'),
                                                     match.group('tag'),
                                                     match.group('name'),
                                                     ))
            pl = self.tab_manager.get_playlist(name)
            pl.name = match.group('name')

            if match.group('tab') not in added_tabs:
                pl = self.add_playlist(pl, erase_empty=False)
                added_tabs[match.group('tab')] = pl
            pl = added_tabs[match.group('tab')]

            if match.group('tag') == 'current':
                count = i
                if self.queue.current_playlist is None:
                    self.queue.set_current_playlist(pl.playlist)
            elif match.group('tag') == 'playing':
                count2 = i
                self.queue.set_current_playlist(pl.playlist)

        # If there's no selected playlist saved, use the currently
        # playing
        if count == -1:
            count = count2

        self.playlist_notebook.set_current_page(count)

    def save_current_tabs(self):
        """
            Saves the open tabs
        """
        # first, delete the current tabs
        names = self.tab_manager.list_playlists()
        for name in names:
            logger.debug("Removing tab %s" % name)
            self.tab_manager.remove_playlist(name)

        for i in range(self.playlist_notebook.get_n_pages()):
            pl = self.playlist_notebook.get_nth_page(i).playlist
            tag = ''
            if pl is self.queue.current_playlist:
                tag = 'playing'
            elif i == self.playlist_notebook.get_current_page():
                tag = 'current'
            pl.name = "order%d.%s.%s" % (i, tag, pl.name)
            logger.debug("Saving tab %d: %s" % (i, pl.name))

            try:
                self.tab_manager.save_playlist(pl, True)
            except:
                # an exception here could cause exaile to be unable to quit.
                # Catch all exceptions.
                import traceback
                traceback.print_exc()

    def add_playlist(self, pl=None, erase_empty=True):
        """
            Adds a playlist to the playlist tab

            @param pl: the xl.playlist.Playlist instance to add
        """
        new_empty_pl = False
        if pl is None:
            pl = xl.playlist.Playlist()
            new_empty_pl = True
        if len(pl.get_tracks()) == 0:
            new_empty_pl = True

        name = pl.get_name()
        nb = self.playlist_notebook
        if pl.get_is_custom():
            for n in range(nb.get_n_pages()):
                nth = nb.get_nth_page(n)
                if nth.playlist.get_is_custom() and not nth.get_needs_save() \
                        and nth.playlist.get_name() == pl.get_name():
                    nb.set_current_page(n)
                    return

        pl = playlist.Playlist(self, self.queue, pl)
        self._connect_playlist_events(pl)

        # Get displayed names for all open playlists, then find a free "slot",
        # e.g. "Playlist 3" if 1 and 2 already exist.
        names = frozenset(nb.get_tab_label(nb.get_nth_page(i)).label.get_text()
            for i in xrange(nb.get_n_pages()))
        i = 1
        try:
            while (name % i) in names:
                i += 1
        except TypeError:
            pass
        else:
            name = name % i

        tab = NotebookTab(self, nb, name, pl)
        # We check if the current playlist is empty, to know if it should be replaced
        cur = nb.get_current_page()
        remove_cur = False
        curpl = nb.get_nth_page(cur)
        if curpl and len(curpl.playlist.get_tracks()) == 0:
            remove_cur = True

        nb.append_page(pl, tab)
        nb.set_tab_reorderable(pl, True)
        if remove_cur and not new_empty_pl and erase_empty:
            nb.remove_page(cur)
            nb.reorder_child(pl, cur)
            nb.set_current_page(cur)
        else:
            nb.set_current_page(nb.get_n_pages() - 1)
        self.set_playlist_modes()

        # Always show tab bar for more than one tab
        if nb.get_n_pages() > 1:
            nb.set_show_tabs(True)

        self.queue.set_current_playlist(pl.playlist)

        return pl

    def _connect_playlist_events(self, pl):
        pl.connect('track-count-changed', lambda *e:
            self.update_track_counts())
        pl.connect('column-settings-changed', self._column_settings_changed)
        pl.list.connect('key-press-event', self._on_pl_key_pressed)

    def _on_pl_key_pressed(self, widget, event):
        if event.keyval == gtk.keysyms.Left:
            # Modifying current position
            if not self.player.current: return
            self.player.scroll(-10)
            self.progress_bar.timer_update() # Needed to evade progressbar lag
        elif event.keyval == gtk.keysyms.Right:
            # Modifying current position
            if not self.player.current: return
            self.player.scroll(10)
            self.progress_bar.timer_update() # Needed to evade progressbar lag

        return False

    def _column_settings_changed(self, *e):
        for page in self.playlist_notebook:
            page.update_col_settings()

    def _setup_hotkeys(self):
        """
            Sets up accelerators that haven't been set up in UI designer
        """
        hotkeys = (
            ('<Control>W', lambda *e: self.close_playlist_tab()),
            ('<Control>S', lambda *e: self.on_save_playlist()),
            ('<Shift><Control>S', lambda *e: self.on_save_playlist_as()),
            ('<Control>F', lambda *e: self.on_search_collection_focus()),
            ('<Control>G', lambda *e: self.on_search_playlist_focus()),
            ('<Control>D', lambda *e: self.on_queue()),
            ('<Control><Alt>l', lambda *e: self.on_clear_queue()),
            ('Left', lambda *e: self._on_left_pressed()),
        )

        self.accel_group = gtk.AccelGroup()
        for key, function in hotkeys:
            key, mod = gtk.accelerator_parse(key)
            self.accel_group.connect_group(key, mod, gtk.ACCEL_VISIBLE,
                function)
        self.window.add_accel_group(self.accel_group)

    def _setup_widgets(self):
        """
            Sets up the various widgets
        """
        if self.controller.exaile.options.Debug:
            logger.info("Enabling Restart menu item")
            restart_item = self.builder.get_object('restart_item')
            restart_item.set_property('visible', True)
            restart_item.set_no_show_all(False)

        # TODO: Maybe make this stackable
        self.message = commondialogs.MessageBar(
            parent=self.builder.get_object('player_box'),
            buttons=gtk.BUTTONS_CLOSE
        )
        self.message.connect('response', self.on_messagebar_response)

        self.info_area = guiutil.TrackInfoPane(auto_update=True)
        self.info_area.set_padding(3, 3, 3, 3)
        guiutil.gtk_widget_replace(self.builder.get_object('info_area'), self.info_area)

        self.cover = cover.CoverWidget(self.info_area.cover_image, self.player)

        self.volume_control = guiutil.VolumeControl()
        self.info_area.get_action_area().pack_start(self.volume_control)

        self.shuffle_toggle = self.builder.get_object('shuffle_button')
        self.shuffle_toggle.connect('button-press-event', self.on_shuffle_pressed)
        self.shuffle_toggle.set_active(settings.get_option('playback/shuffle', False))
        self.shuffle_image = self.builder.get_object('shuffle_button_image')

        self.repeat_toggle = self.builder.get_object('repeat_button')
        self.repeat_toggle.connect('button-press-event', self.on_repeat_pressed)
        self.repeat_toggle.set_active(settings.get_option('playback/repeat', False))

        self.dynamic_toggle = self.builder.get_object('dynamic_button')
        self.dynamic_toggle.set_active(settings.get_option('playback/dynamic', False))
        self.update_dynamic_toggle()

        self.progress_bar = PlaybackProgressBar(
            self.builder.get_object('playback_progressbar'),
            self.player
        )

        for button in ('playpause', 'next', 'prev', 'stop'):
            setattr(self, '%s_button' % button,
                self.builder.get_object('%s_button' % button))

        self.stop_button.add_events(gtk.gdk.POINTER_MOTION_MASK)
        self.stop_button.connect('motion-notify-event',
            self.on_stop_button_motion_notify_event)
        self.stop_button.connect('leave-notify-event',
            self.on_stop_button_leave_notify_event)
        self.stop_button.connect('key-press-event',
            self.on_stop_button_key_press_event)
        self.stop_button.connect('key-release-event',
            self.on_stop_button_key_release_event)
        self.stop_button.connect('focus-out-event',
            self.on_stop_button_focus_out_event)
        self.stop_button.connect('button-press-event',
            self.on_stop_button_press_event)

        self.statusbar = guiutil.Statusbar(self.builder.get_object('status_bar'))

        self.filter = guiutil.SearchEntry(
            self.builder.get_object('playlist_search_entry'))

    def on_expose_event(self, widget, event):
        """
            Paints the window alpha transparency
        """
        opacity = 1 - settings.get_option('gui/transparency', 0.3)
        context = widget.window.cairo_create()
        background = widget.style.bg[gtk.STATE_NORMAL]
        context.set_source_rgba(
            float(background.red) / 256**2,
            float(background.green) / 256**2,
            float(background.blue) / 256**2,
            opacity
        )
        context.set_operator(cairo.OPERATOR_SOURCE)
        context.paint()

    def on_screen_changed(self, widget, event):
        """
            Updates the colormap on screen change
        """
        screen = widget.get_screen()
        colormap = screen.get_rgba_colormap() or screen.get_rgb_colormap()
        self.window.set_colormap(rgbamap)

    def on_messagebar_response(self, widget, response):
        """
            Hides the messagebar if requested
        """
        if response == gtk.RESPONSE_CLOSE:
            widget.hide()

    def on_queue(self):
        """
            Toggles queue on the current playlist
        """
        cur_page = self.playlist_notebook.get_children()[
                self.playlist_notebook.get_current_page()]
        cur_page.menu.on_queue()

    def on_playlist_utilities_bar_visible_toggled(self, checkmenuitem):
        """
            Shows or hides the playlist utilities bar
        """
        settings.set_option('gui/playlist_utilities_bar_visible',
            checkmenuitem.get_active())

    def on_stop_button_motion_notify_event(self, widget, event):
        """
            Sets the hover state and shows SPAT icon
        """
        widget.set_data('hovered', True)
        if event.state & gtk.gdk.SHIFT_MASK:
            widget.set_image(gtk.image_new_from_stock(
                gtk.STOCK_STOP, gtk.ICON_SIZE_BUTTON))
        else:
            widget.set_image(gtk.image_new_from_stock(
                gtk.STOCK_MEDIA_STOP, gtk.ICON_SIZE_BUTTON))

    def on_stop_button_leave_notify_event(self, widget, event):
        """
            Unsets the hover state and resets the button icon
        """
        widget.set_data('hovered', False)
        if not widget.is_focus() and \
           ~(event.state & gtk.gdk.SHIFT_MASK):
            widget.set_image(gtk.image_new_from_stock(
                gtk.STOCK_MEDIA_STOP, gtk.ICON_SIZE_BUTTON))

    def on_stop_button_key_press_event(self, widget, event):
        """
            Shows SPAT icon on Shift key press
        """
        if event.keyval in (gtk.keysyms.Shift_L, gtk.keysyms.Shift_R):
            widget.set_image(gtk.image_new_from_stock(
                gtk.STOCK_STOP, gtk.ICON_SIZE_BUTTON))
            widget.set_data('toggle_spat', True)

        if event.keyval in (gtk.keysyms.space, gtk.keysyms.Return):
            if widget.get_data('toggle_spat'):
                self.on_spat_clicked()
            else:
                self.player.stop()

    def on_stop_button_key_release_event(self, widget, event):
        """
            Resets the button icon
        """
        if event.keyval in (gtk.keysyms.Shift_L, gtk.keysyms.Shift_R):
            widget.set_image(gtk.image_new_from_stock(
                gtk.STOCK_MEDIA_STOP, gtk.ICON_SIZE_BUTTON))
            widget.set_data('toggle_spat', False)

    def on_stop_button_focus_out_event(self, widget, event):
        """
            Resets the button icon unless
            the button is still hovered
        """
        if not widget.get_data('hovered'):
            widget.set_image(gtk.image_new_from_stock(
                gtk.STOCK_MEDIA_STOP, gtk.ICON_SIZE_BUTTON))

    def on_stop_button_press_event(self, widget, event):
        """
            Called when the user clicks on the stop button
        """
        if event.button == 1:
            if event.state & gtk.gdk.SHIFT_MASK:
                self.on_spat_clicked()
            else:
                self.player.stop()
        elif event.button == 3:
            menu = guiutil.Menu()
            menu.append(_("Toggle: Stop after Selected Track"),
                self.on_spat_clicked,
                gtk.STOCK_STOP)
            menu.popup(None, None, None, event.button, event.time)

    def on_spat_clicked(self, *e):
        """
            Called when the user clicks on the SPAT item
        """
        trs = self.get_selected_playlist().get_selected_tracks()
        if not trs: return
        tr = trs[0]

        if tr == self.queue.stop_track:
            self.queue.stop_track = None
        else:
            self.queue.stop_track = tr

        self.get_selected_playlist().list.queue_draw()

    def update_track_counts(self, *e):
        """
            Updates the track count information
        """
        if not self.get_selected_playlist(): return

        self.statusbar.set_track_count(
            len(self.get_selected_playlist().playlist),
            self.collection.get_count())
        self.statusbar.set_queue_count(len(self.queue))

    def update_dynamic_toggle(self, *e):
        """
            Shows or hides the dynamic toggle button
            based on the amount of providers available
        """
        providers_available = len(providers.get('dynamic_playlists')) > 0
        if providers_available:
            self.dynamic_toggle.set_sensitive(True)
            self.dynamic_toggle.set_tooltip_text(
                _('Dynamically add similar tracks to the playlist')
            )
        else:
            self.dynamic_toggle.set_sensitive(False)
            self.dynamic_toggle.set_tooltip_text(
                _('Requires plugins providing dynamic playlists')
            )

    def _connect_events(self):
        """
            Connects the various events to their handlers
        """
        self.splitter.connect('notify::position', self.configure_event)
        self.builder.connect_signals({
            'on_configure_event':   self.configure_event,
            'on_window_state_event': self.window_state_change_event,
            'on_delete_event':      self.delete_event,
            'on_quit_item_activated': self.quit,
            'on_restart_item_activate': self.on_restart_item_activate,
            'on_playpause_button_clicked': self.on_playpause_button_clicked,
            'on_next_button_clicked':
                lambda *e: self.queue.next(),
            'on_prev_button_clicked':
                lambda *e: self.queue.prev(),
            'on_repeat_button_toggled': self.set_mode_toggles,
            'on_dynamic_button_toggled': self.set_mode_toggles,
            'on_playlist_search_entry_activate': self.on_playlist_search_entry_activate,
            'on_clear_playlist_button_clicked': self.on_clear_playlist,
            'on_playlist_notebook_switch':  self.on_playlist_notebook_switch,
            'on_playlist_notebook_remove': self.on_playlist_notebook_remove,
            'on_playlist_notebook_button_press': self.on_playlist_notebook_button_press,
            'on_new_playlist_item_activated': lambda *e:
                self.add_playlist(),
            'on_queue_count_clicked': self.controller.queue_manager,
            # Controller
            'on_about_item_activate': self.controller.show_about_dialog,
            'on_scan_collection_item_activate': self.controller.on_rescan_collection,
            'on_randomize_playlist_item_activate': self.controller.on_randomize_playlist,
            'on_collection_manager_item_activate': self.controller.collection_manager,
            'on_goto_playing_track_activate': self.controller.on_goto_playing_track,
            'on_queue_manager_item_activate': self.controller.queue_manager,
            'on_preferences_item_activate': lambda *e: self.controller.show_preferences(),
            'on_device_manager_item_activate': lambda *e: self.controller.show_devices(),
            'on_cover_manager_item_activate': self.controller.show_cover_manager,
            'on_open_item_activate': self.controller.open_dialog,
            'on_open_url_item_activate': self.controller.open_url,
            'on_open_dir_item_activate': self.controller.open_dir,
            'on_export_current_playlist_activate': self.controller.export_current_playlist,
            'on_panel_notebook_switch_page': self.controller.on_panel_switch,
            'on_track_properties_activate':self.controller.on_track_properties,
            'on_clear_playlist_item_activate': self.on_clear_playlist,
            'on_playlist_utilities_bar_visible_toggled': self.on_playlist_utilities_bar_visible_toggled,
        })

        event.add_callback(self.on_playback_resume, 'playback_player_resume',
            self.player)
        event.add_callback(self.on_playback_end, 'playback_player_end',
            self.player)
        event.add_callback(self.on_playback_end, 'playback_error',
            self.player)
        event.add_callback(self.on_playback_start, 'playback_track_start',
            self.player)
        event.add_callback(self.on_toggle_pause, 'playback_toggle_pause',
            self.player)
        event.add_callback(self.on_tags_parsed, 'tags_parsed',
            self.player)
        event.add_callback(self.on_track_tags_changed, 'track_tags_changed')
        event.add_callback(self.on_buffering, 'playback_buffering',
            self.player)
        event.add_callback(self.on_playback_error, 'playback_error',
            self.player)

        # Dynamic toggle button
        event.add_callback(self.update_dynamic_toggle,
            'dynamic_playlists_provider_added')
        event.add_callback(self.update_dynamic_toggle,
            'dynamic_playlists_provider_removed')

        # Monitor the queue
        event.add_callback(self.update_track_counts,
            'tracks_added', self.queue)
        event.add_callback(self.update_track_counts,
            'tracks_removed', self.queue)

        event.add_callback(self.queue_playlist_draw, 'stop_track', self.queue)

        # Settings
        event.add_callback(self._on_setting_change, 'option_set')

    def queue_playlist_draw(self, *e):
        self.get_selected_playlist().list.queue_draw()

    def _connect_panel_events(self):
        """
            Sets up panel events
        """
        # panels
        panels = self.controller.panels

        for panel_name in ('playlists', 'radio', 'files', 'collection'):
            panel = panels[panel_name]
            sort = False

            if panel_name in ('files', 'collection'):
                sort = True

            panel.connect('append-items', lambda panel, items, sort=sort:
                self.on_append_items(items, sort=sort))
            panel.connect('queue-items', lambda panel, items, sort=sort:
                self.on_append_items(items, queue=True, sort=sort))
            panel.connect('replace-items', lambda panel, items, sort=sort:
                self.on_append_items(items, replace=True, sort=sort))

        ## Collection Panel
        panel = panels['collection']
        panel.connect('collection-tree-loaded', lambda *e:
            self.update_track_counts())

        ## Playlist Panel
        panel = panels['playlists']
        panel.connect('playlist-selected',
            lambda panel, playlist: self.add_playlist(playlist))

        ## Radio Panel
        panel = panels['radio']
        panel.connect('playlist-selected',
            lambda panel, playlist: self.add_playlist(playlist))

        ## Files Panel
        panel = panels['files']

    def on_append_items(self, tracks, queue=False, sort=False, replace=False):
        """
            Called when a panel (or other component)
            has tracks to append and possibly queue

            :param tracks: The tracks to append
            :param queue: Additionally queue tracks
            :param sort: Sort before adding
            :param replace: Clear playlist before adding
        """
        if not tracks:
            return

        pl = self.get_selected_playlist()

        if sort:
            tracks = trax.sort_tracks(
                ('artist', 'date', 'album', 'discnumber', 'tracknumber'),
                tracks)

        if replace:
            pl.playlist.clear()

        pl.playlist.add_tracks(tracks)

        if queue:
            self.queue.add_tracks(tracks)

        pl.list.queue_draw()

        if not self.player.current:
            track = tracks[0]
            index = pl.playlist.index(track)
            pl.playlist.set_current_pos(index)
            self.queue.play(track=track)
            self.queue.set_current_playlist(pl.playlist)

    def on_playback_error(self, type, player, message):
        """
            Called when there has been a playback error
        """
        self.message.show_error(_('Playback error encountered!'), message)

    def on_buffering(self, type, player, percent):
        """
            Called when a stream is buffering
        """
        percent = min(percent, 100)
        self.statusbar.set_status(_("Buffering: %d%%...") % percent, 1)

    def on_tags_parsed(self, type, player, args):
        """
            Called when tags are parsed from a stream/track
        """
        (tr, args) = args
        if not tr or tr.is_local():
            return
        if player.parse_stream_tags(tr, args):
            self._update_track_information()
            self.cover.on_playback_start('', self.player, None)
            self.get_selected_playlist().refresh_row(tr)

    def on_track_tags_changed(self, type, track, tag):
        """
            Called when tags are changed
        """
        if track is self.player.current:
            self._update_track_information()

    def on_toggle_pause(self, type, player, object):
        """
            Called when the user clicks the play button after playback has
            already begun
        """
        if player.is_paused():
            image = gtk.image_new_from_stock(gtk.STOCK_MEDIA_PLAY,
                gtk.ICON_SIZE_SMALL_TOOLBAR)
            tooltip = _('Continue Playback')
        else:
            image = gtk.image_new_from_stock(gtk.STOCK_MEDIA_PAUSE,
                gtk.ICON_SIZE_SMALL_TOOLBAR)
            tooltip = _('Pause Playback')

        self.playpause_button.set_image(image)
        self.playpause_button.set_tooltip_text(tooltip)
        self._update_track_information()

        # refresh the current playlist
        pl = self.get_selected_playlist()
        if pl:
            pl.list.queue_draw()

    def close_playlist_tab(self, tab=None):
        """
            Closes the tab specified
            @param tab: the tab number to close.  If no number is specified,
                the currently selected tab is closed
        """
        if tab is None:
            tab = self.playlist_notebook.get_current_page()
        pl = self.playlist_notebook.get_nth_page(tab)
        if pl.on_closing():
            if self.queue.current_playlist == pl.playlist:
                self.queue.current_playlist = None
            self.playlist_notebook.remove_page(tab)

    def on_playlist_notebook_switch(self, notebook, page, page_num):
        """
            Called when the page is changed in the playlist notebook
        """
        page = notebook.get_nth_page(page_num)
        self.current_page = page_num
        playlist = self.get_selected_playlist()
        self.queue.set_current_playlist(playlist.playlist)
        self.set_playlist_modes()
        self._on_setting_change(None, None, 'playback/shuffle')
        self._on_setting_change(None, None, 'playback/shuffle_mode')
        self.update_track_counts()

    def on_playlist_notebook_remove(self, notebook, widget):
        """
            Called when a tab is removed from the playlist notebook
        """
        pagecount = notebook.get_n_pages()
        if pagecount == 1:
            notebook.set_show_tabs(settings.get_option('gui/show_tabbar', True))
        elif pagecount == 0:
            self.add_playlist()

    def on_playlist_notebook_button_press(self, notebook, event):
        if event.type == gtk.gdk.BUTTON_PRESS and event.button == 2:
            self.add_playlist()

    def on_search_collection_focus(self, *e):
        """
            Gives focus to the collection search bar
        """

        self.controller.panels['collection'].filter.grab_focus()

    def on_search_playlist_focus(self, *e):
        """
            Gives focus to the playlist search bar
        """
        self.filter.grab_focus()

    def on_playlist_search_entry_activate(self, entry):
        """
            Starts searching the current playlist
        """
        playlist = self.get_selected_playlist()
        if playlist:
            playlist.search(unicode(entry.get_text(), 'utf-8'))

    def on_save_playlist(self, *e):
        """
            Called when the user presses Ctrl+S
            Spawns the save dialog of the currently selected playlist tab if
            not custom, saves changes directly if custom
        """
        tab = self.get_selected_tab()
        if not tab: return
        if tab.page.playlist.get_is_custom():
            tab.do_save_changes_to_custom()
        else:
            tab.do_save_custom()

    def on_save_playlist_as(self, *e):
        """
            Called when the user presses Ctrl+S
            Spawns the save as dialog of the current playlist tab
        """
        tab = self.get_selected_tab()
        if not tab: return
        tab.do_save_custom()

    def on_clear_queue(self):
        """
            Called when the user requests to clear the queue
        """
        self.queue.clear()
        self.queue_playlist_draw()

    def on_clear_playlist(self, *e):
        """
            Clears the current playlist tab
        """
        playlist = self.get_selected_playlist()
        if not playlist: return
        playlist.playlist.clear()

    def on_shuffle_pressed(self, widget, event):
        """
            Called when the shuffle button is clicked
        """
        #Make it appear pressed in when the menu pops up (looks a bit nicer)
        self.shuffle_toggle.set_active(True)

        #Get the current setting so the right radio button is chosen
        sel = [False, False, False]
        if settings.get_option('playback/shuffle', False) == True:
            if settings.get_option('playback/shuffle_mode') == 'track':
                sel[1] = True
            else:
                sel[2] = True
        else:
            sel[0] = True


        #SHUFFLE POPUP MENU
        menu = gtk.Menu()

        #Connect signal to make sure the toggle goes back to how it should be
        #after we changed it when the menu was popped up for asthetics
        menu.connect("deactivate", lambda *e: self.shuffle_toggle.set_active(
                    settings.get_option('playback/shuffle', False)))
        texts = (_("Shuffle _Off"), _("Shuffle _Tracks"), _("Shuffle _Albums"))
        r = None
        for num, text in enumerate(texts):
            r = gtk.RadioMenuItem(r, text)
            r.set_active(sel[num])
            menu.append(r)
            r.connect("activate", self.shuffle_mode_selected, text)
            r.show()
            if text == _("Shuffle _Off"):
                sep = gtk.SeparatorMenuItem()
                menu.append(sep)
                sep.show()

        menu.popup(None, None, self.mode_menu_set_pos, event.button, event.time, widget)
        #Call reposition as the menu's width is required in calculation and
        #it needs a "refresh"
        menu.reposition()

    def on_repeat_pressed(self, widget, event):
        """
            Called when the repeat button is clicked
        """
        #Make it appear pressed in when the menu pops up (looks a bit nicer)
        self.repeat_toggle.set_active(True)

        #Get the current setting so the right radio button is chosen
        sel = [False, False, False]
        if settings.get_option('playback/repeat', False) == True:
            if settings.get_option('playback/repeat_mode') == 'playlist':
                sel[1] = True
            else:
                sel[2] = True
        else:
            sel[0] = True


        #REPEAT POPUP MENU
        menu = gtk.Menu()

        #Connect signal to make sure the toggle goes back to how it should be
        #after we changed it when the menu was popped up for asthetics
        menu.connect("deactivate", lambda *e: self.repeat_toggle.set_active(
                    settings.get_option('playback/repeat', False)))
        texts = (_("Repeat _Off"), _("Repeat _Playlist"), _("Repeat _Track"))
        r = None
        for num, text in enumerate(texts):
            r = gtk.RadioMenuItem(r, text)
            r.set_active(sel[num])
            menu.append(r)
            r.connect("activate", self.repeat_mode_selected, text)
            r.show()
            if text == _("Repeat _Off"):
                sep = gtk.SeparatorMenuItem()
                menu.append(sep)
                sep.show()

        menu.popup(None, None, self.mode_menu_set_pos, event.button, event.time, widget)
        #Call reposition as the menu's width is required in calculation and
        #it needs a "refresh"
        menu.reposition()

    def mode_menu_set_pos(self, menu, button):
        """
            Nicely position the shuffle popup menu with the button's corner
        """
        w = self.window.get_position()
        b = button.get_allocation()
        m = menu.get_allocation()
        pos = (w[0] + b.x + 1,
                w[1] + b.y + b.height - m.height - 3)

        return (pos[0], pos[1], True)

    def shuffle_mode_selected(self, widget, mode):

        if mode == _("Shuffle _Off"):
            settings.set_option('playback/shuffle', False)
        elif mode == _("Shuffle _Tracks"):
            settings.set_option('playback/shuffle', True)
            settings.set_option('playback/shuffle_mode', 'track')
        elif mode == _("Shuffle _Albums"):
            settings.set_option('playback/shuffle', True)
            settings.set_option('playback/shuffle_mode', 'album')

    def repeat_mode_selected(self, widget, mode):

        if mode == _("Repeat _Off"):
            settings.set_option('playback/repeat', False)
        elif mode == _("Repeat _Playlist"):
            settings.set_option('playback/repeat', True)
            settings.set_option('playback/repeat_mode', 'playlist')
        elif mode == _("Repeat _Track"):
            settings.set_option('playback/repeat', True)
            settings.set_option('playback/repeat_mode', 'track')

    def set_mode_toggles(self, *e):
        """
            Called when the user clicks one of the playback mode buttons
        """
        settings.set_option('playback/repeat',
                self.repeat_toggle.get_active())
        settings.set_option('playback/dynamic',
                self.dynamic_toggle.get_active())

    def set_playlist_modes(self):
        pl = self.get_selected_playlist()
        if pl:
            pl.playlist.set_random(settings.get_option('playback/shuffle'),
                settings.get_option('playback/shuffle_mode'))
            pl.playlist.set_repeat(settings.get_option('playback/repeat'),
                settings.get_option('playback/repeat_mode'))

    def on_playback_resume(self, type, player, data):
        self.resuming = True

    def on_playback_start(self, type, player, object):
        """
            Called when playback starts
            Sets the currently playing track visible in the currently selected
            playlist if the user has chosen this setting
        """
        if self.resuming:
            self.resuming = False
            return

        pl = self.get_selected_playlist()
        if player.current in pl.playlist.ordered_tracks:
            path = (pl.playlist.index(player.current),)

            if settings.get_option('gui/ensure_visible', True):
                pl.list.scroll_to_cell(path)

            glib.idle_add(pl.list.set_cursor, path)

        self._update_track_information()
        self.draw_playlist(type, player, object)
        self.playpause_button.set_image(gtk.image_new_from_stock(gtk.STOCK_MEDIA_PAUSE,
                gtk.ICON_SIZE_SMALL_TOOLBAR))
        self.playpause_button.set_tooltip_text(_('Pause Playback'))
        self.update_track_counts()

        if settings.get_option('playback/dynamic', False):
            self._get_dynamic_tracks()

        if settings.get_option('osd/enabled', True):
            self.osd.show(self.player.current)

    def on_playback_end(self, type, player, object):
        """
            Called when playback ends
        """
        self.window.set_title('Exaile')
        self._update_track_information()

        self.draw_playlist(type, player, object)
        self.playpause_button.set_image(gtk.image_new_from_stock(gtk.STOCK_MEDIA_PLAY,
                gtk.ICON_SIZE_SMALL_TOOLBAR))
        self.playpause_button.set_tooltip_text(_('Start Playback'))

    def _on_setting_change(self, name, object, option):
        """
           Handles changes of settings
        """
        if option == 'gui/show_tabbar':
            self.playlist_notebook.set_show_tabs(
                settings.get_option(option, True)
            )

        if option == 'gui/tab_placement':
            map = {
                'left': gtk.POS_LEFT,
                'right': gtk.POS_RIGHT,
                'top': gtk.POS_TOP,
                'bottom': gtk.POS_BOTTOM
            }
            self.playlist_notebook.set_tab_pos(map.get(
                settings.get_option(option, 'top')))

        if option == 'gui/playlist_utilities_bar_visible':
            visible = settings.get_option(option, True)
            self.playlist_utilities_bar.set_property('visible', visible)
            self.playlist_utilities_bar.set_sensitive(visible)
            self.playlist_utilities_bar.set_no_show_all(not visible)

        if option == 'gui/use_tray':
            usetray = settings.get_option(option, False)
            if self.controller.tray_icon and not usetray:
                self.controller.tray_icon.destroy()
                self.controller.tray_icon = None
            elif not self.controller.tray_icon and usetray:
                self.controller.tray_icon = tray.TrayIcon(self)

        if option == 'playback/dynamic':
            self.dynamic_toggle.set_active(settings.get_option(option, False))

        if option == 'playback/shuffle':
            self.shuffle_toggle.set_active(settings.get_option(option, False))
            if settings.get_option(option, False) == False:
                self.shuffle_image.set_from_icon_name('media-playlist-shuffle',
                        gtk.ICON_SIZE_BUTTON)
            else:
                if settings.get_option('playback/shuffle_mode') == "track":
                    self.shuffle_image.set_from_icon_name('media-playlist-shuffle',
                            gtk.ICON_SIZE_BUTTON)
                else:
                    self.shuffle_image.set_from_icon_name('media-optical',
                            gtk.ICON_SIZE_BUTTON)

            self.set_playlist_modes()


        if option == 'playback/shuffle_mode':
            if settings.get_option(option) == "track":
                self.shuffle_image.set_from_icon_name('media-playlist-shuffle',
                        gtk.ICON_SIZE_BUTTON)
            else:
                if settings.get_option('playback/shuffle'):
                    self.shuffle_image.set_from_icon_name('media-optical',
                            gtk.ICON_SIZE_BUTTON)
                else:
                    self.shuffle_image.set_from_icon_name('media-playlist-shuffle',
                            gtk.ICON_SIZE_BUTTON)

            self.set_playlist_modes()

        if option == 'playback/repeat':
            self.repeat_toggle.set_active(settings.get_option(option, False))

            self.set_playlist_modes()

        if option == 'playback/repeat_mode':
            self.set_playlist_modes()


    @common.threaded
    def _get_dynamic_tracks(self):
        """
            Gets some dynamic tracks from the dynamic manager.

            This tries to keep at least 5 tracks the current playlist... if
            there are already 5, it just adds one
        """
        playlist = self.get_selected_playlist().playlist
        self.controller.exaile.dynamic.populate_playlist(playlist)

    def _update_track_information(self):
        """
            Sets track information
        """
        track = self.player.current

        if not track:
            return

        artist = track.get_tag_display('artist', artist_compilations=False)
        album = track.get_tag_display('album')
        title = track.get_tag_display('title')

        # Update window title.
        if artist:
            # TRANSLATORS: Window title
            self.window.set_title(_("%(title)s (by %(artist)s)") %
                { 'title': title, 'artist': artist } + " - Exaile")
        else:
            self.window.set_title(title + " - Exaile")

    def draw_playlist(self, *e):
        """
            Called when playback starts, redraws the playlist
        """
        page = self.playlist_notebook.get_current_page()
        page = self.playlist_notebook.get_nth_page(page)
        glib.idle_add(page.queue_draw)

    def get_selected_playlist(self):
        """
            Returns the currently selected playlist
        """
        page = self.playlist_notebook.get_nth_page(self.current_page)
        if page: return page
        num = self.playlist_notebook.get_current_page()
        page = self.playlist_notebook.get_nth_page(num)
        return page

    get_current_playlist = get_selected_playlist

    def get_selected_tab(self):
        """
            Returns the currently selected tab
        """
        page = self.playlist_notebook.get_nth_page(self.current_page)
        if not page:
            num = self.playlist_notebook.get_current_page()
            page = self.playlist_notebook.get_nth_page(num)
        return self.playlist_notebook.get_tab_label(page)

    get_current_tab = get_selected_tab

    def on_playpause_button_clicked(self, *e):
        """
            Called when the play button is clicked
        """
        if self.player.is_paused() or self.player.is_playing():
            self.player.toggle_pause()
        else:
            pl = self.get_selected_playlist()
            self.queue.set_current_playlist(pl.playlist)
            if pl:
                track = pl.get_selected_track()
                if track:
                    pl.playlist.set_current_pos(
                        pl.playlist.index(track))
            self.queue.play()

    def _setup_position(self):
        """
            Sets up the position and sized based on the size the window was
            when it was last moved or resized
        """
        if settings.get_option('gui/mainw_maximized', False):
            self.window.maximize()

        width = settings.get_option('gui/mainw_width', 500)
        height = settings.get_option('gui/mainw_height', 475)
        x = settings.get_option('gui/mainw_x', 10)
        y = settings.get_option('gui/mainw_y', 10)

        self.window.move(x, y)
        self.window.resize(width, height)

        pos = settings.get_option('gui/mainw_sash_pos', 200)
        self.splitter.set_position(pos)

    def delete_event(self, *e):
        """
            Called when the user attempts to close the window
        """
        if self.controller.tray_icon:
            self.window.hide()
        else:
            self.quit()
        return True

    def quit(self, *e):
        """
            Quits Exaile
        """
        self.window.hide()
        glib.idle_add(self.controller.exaile.quit)
        return True

    def on_restart_item_activate(self, menuitem):
        """
            Restarts Exaile
        """
        self.window.hide()
        glib.idle_add(self.controller.exaile.quit, True)

    def toggle_visible(self, bringtofront=False):
        """
            Toggles visibility of the main window
        """
        toggle_handled = self.emit('main-visible-toggle')

        if not toggle_handled:
            if bringtofront and self.window.is_active() or \
               not bringtofront and self.window.get_property('visible'):
                self.window.hide()
            elif not toggle_handled:
                self.window.present()

    def configure_event(self, *e):
        """
            Called when the window is resized or moved
        """
        pos = self.splitter.get_position()
        if pos > 10 and pos != settings.get_option(
                "gui/mainw_sash_pos", -1):
            settings.set_option('gui/mainw_sash_pos', pos)

        # Don't save window size if it is maximized or fullscreen.
        if settings.get_option('gui/mainw_maximized', False) or \
                self._fullscreen:
            return False

        (width, height) = self.window.get_size()
        if [width, height] != [ settings.get_option("gui/mainw_"+key, -1) for \
                key in ["width", "height"] ]:
            settings.set_option('gui/mainw_height', height)
            settings.set_option('gui/mainw_width', width)
        (x, y) = self.window.get_position()
        if [x, y] != [ settings.get_option("gui/mainw_"+key, -1) for \
                key in ["x", "y"] ]:
            settings.set_option('gui/mainw_x', x)
            settings.set_option('gui/mainw_y', y)

        return False

    def window_state_change_event(self, window, event):
        """
            Saves the current maximized and fullscreen
            states and minimizes to tray if requested
        """
        if event.changed_mask & gtk.gdk.WINDOW_STATE_MAXIMIZED:
            settings.set_option('gui/mainw_maximized',
                bool(event.new_window_state & gtk.gdk.WINDOW_STATE_MAXIMIZED))
        if event.changed_mask & gtk.gdk.WINDOW_STATE_FULLSCREEN:
            self._fullscreen = bool(event.new_window_state & gtk.gdk.WINDOW_STATE_FULLSCREEN)

        if settings.get_option('gui/minimize_to_tray', False):
            wm_state = window.window.property_get('_NET_WM_STATE')

            if wm_state is not None:
                if '_NET_WM_STATE_HIDDEN' in wm_state[2]:
                    if not settings.get_option('gui/use_tray', False) and \
                        self.controller.tray_icon is None:
                        self.controller.tray_icon = tray.TrayIcon(self)
                    window.hide()
                else:
                    if not settings.get_option('gui/use_tray', False) and \
                        self.controller.tray_icon is not None:
                        self.controller.tray_icon.destroy()
                        self.controller.tray_icon = None

        return False

def get_playlist_notebook():
    return MainWindow._mainwindow.playlist_notebook

def get_selected_playlist():
    return MainWindow._mainwindow.get_selected_playlist()

def mainwindow():
    return MainWindow._mainwindow

# vim: et sts=4 sw=4
