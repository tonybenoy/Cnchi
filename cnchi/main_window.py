#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# main_window.py
#
# Copyright © 2013-2015 Antergos
#
# This file is part of Cnchi.
#
# Cnchi is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# Cnchi is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# The following additional terms are in effect as per Section 7 of the license:
#
# The preservation of all legal notices and author attributions in
# the material or in the Appropriate Legal Notices displayed
# by works containing it is required.
#
# You should have received a copy of the GNU General Public License
# along with Cnchi; If not, see <http://www.gnu.org/licenses/>.


""" Main Cnchi Window """

import os
import sys
import time
import multiprocessing
import logging
import config
import welcome
import language
import location
import check
import desktop
import desktop_info
import features
import keymap
import timezone
import user_info
import slides
import summary
import info
import substack
import gtkbasebox

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, Atk

import show_message as show
import misc.extra as misc

from installation import ask as installation_ask
from installation import automatic as installation_automatic
from installation import alongside as installation_alongside
from installation import advanced as installation_advanced
from installation import zfs as installation_zfs


def atk_set_image_description(widget, description):
    """ Sets the textual description for a widget that displays image/pixmap
        information onscreen. """
    atk_widget = widget.get_accessible()
    if atk_widget is not None:
        atk_widget.set_image_description(description)


class MainWindow(Gtk.ApplicationWindow):
    """ Cnchi main window """

    def __init__(self, app, cmd_line):
        super().__init__(title="Cnchi", application=app)

        self.cnchi_app = app
        self._main_window_width = 960
        self._main_window_height = 600

        logging.info("Cnchi installer version %s", info.CNCHI_VERSION)

        self.settings = config.Settings()
        self.ui_dir = self.settings.get('ui')
        self.cmd_line = cmd_line
        self.params = dict()
        self.data_dir = self.settings.get('data')
        self.current_stack = None
        self.current_grp = None
        self.current_page = None
        self.next_page = None
        self.prev_page = None
        self.nav_buttons = {}
        self.sub_nav_btns = {}
        self.page_count = 0
        self.all_pages = []
        self.pages_loaded = False
        self.stacks = []
        self.cnchi_started = False
        self.timezone_start_needed = False

        # By default, always try to use local /var/cache/pacman/pkg
        self.xz_cache = ["/var/cache/pacman/pkg"]

        # Check command line
        if cmd_line.cache and cmd_line.cache not in self.xz_cache:
            self.xz_cache.append(cmd_line.cache)

        # Log cache dirs
        for xz in self.xz_cache:
            logging.debug("Cnchi will use '%s' as a source for cached xz packages", xz)

        # Store cache dirs in config
        self.settings.set('xz_cache', self.xz_cache)

        # For things we are not ready for users to test
        self.settings.set('z_hidden', cmd_line.z_hidden)

        # Set enabled desktops
        if self.settings.get('z_hidden'):
            self.settings.set("desktops", desktop_info.DESKTOPS_DEV)
        else:
            self.settings.set("desktops", desktop_info.DESKTOPS)

        if cmd_line.environment:
            my_desktop = cmd_line.environment.lower()
            if my_desktop in desktop_info.DESKTOPS:
                self.settings.set('desktop', my_desktop)
                self.settings.set('desktop_ask', False)
                logging.debug("Cnchi will install the %s desktop environment", my_desktop)

        # Create a queue. Will be used to report pacman messages
        # (pacman/pac.py) to the main thread (installation/process.py)
        self.callback_queue = multiprocessing.JoinableQueue()

        # This list will have all processes (rankmirrors, autotimezone...)
        self.process_list = []

        # Initialize GUI elements
        self.initialize_gui()

        # Prepare params dict to pass common parameters to all screens
        self.prepare_shared_parameters()

        self.language_widget = language.LanguageWidget(self.params, button=self.language_menu_btn)
        self.popover = Gtk.Popover.new(self.language_menu_btn)
        self.popover.add(self.language_widget)
        self.popover.set_position(Gtk.PositionType.BOTTOM)
        self.popover.connect('closed', self.on_language_popover_closed)

        if cmd_line.packagelist:
            self.settings.set('alternate_package_list', cmd_line.packagelist)
            logging.info(
                "Using '%s' file as package list",
                self.settings.get('alternate_package_list'))

        self.pre_load_pages()

        self.main_box.add(self.current_page)

        self.settings.set('timezone_start', True)
        self.connect('delete-event', self.on_exit_button_clicked)
        self.connect('key-release-event', self.on_key_release)

        self.ui.connect_signals(self)
        self.header_ui.connect_signals(self)

        self.set_geometry()

        # Set window icon
        icon_path = os.path.join(self.data_dir, "images", "antergos", "antergos-ball.png")
        self.set_icon_from_file(icon_path)

        # Use our css file
        style_provider = Gtk.CssProvider()

        style_css = os.path.join(self.data_dir, "css", "gtk-style.css")

        with open(style_css, 'rb') as css:
            css_data = css.read()

        style_provider.load_from_data(css_data)

        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), style_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_USER
        )

        self.current_page.prepare('forwards')

        # Show main window
        self.show_all()

        # Hide backwards button
        self.backwards_button.hide()

        self.progressbar.set_fraction(0)
        self.progressbar_step = 0

        # Hide the progress bar for default iso
        if not os.path.exists('/home/antergos/.config/openbox'):
            self.progressbar.hide()

        self.set_focus(None)

        misc.gtk_refresh()

        self.cnchi_started = True
        if self.timezone_start_needed:
            self.on_has_internet_connection()

    def prepare_shared_parameters(self):
        """
        Parameters that are common to all screens

        """

        self.params['main_window'] = self
        self.params['main_box_wrapper'] = self.main_box_wrapper
        self.params['header'] = self.header
        self.params['ui_dir'] = self.ui_dir
        self.params['forward_button'] = self.forward_button
        self.params['backwards_button'] = self.backwards_button
        self.params['callback_queue'] = self.callback_queue
        self.params['settings'] = self.settings
        self.params['main_progressbar'] = self.progressbar
        self.params['process_list'] = self.process_list
        self.params['checks_are_optional'] = self.cmd_line.no_check
        self.params['disable_tryit'] = self.cmd_line.disable_tryit
        self.params['disable_rank_mirrors'] = self.cmd_line.disable_rank_mirrors

    def initialize_gui(self):
        """
        Initial setup of our UI elements. This must be called during __init__().

        """

        self.ui = Gtk.Builder()
        path = os.path.join(self.ui_dir, "cnchi.ui")
        self.ui.add_from_file(path)
        self.add(self.ui.get_object("main"))

        self.main_box_wrapper = self.ui.get_object("main_box_wrapper")
        self.main_box = self.ui.get_object("main_box")
        self.main_stack = self.ui.get_object("main_stack")
        self.sub_nav_box = self.ui.get_object("sub_nav_box")

        self.main_stack.set_transition_type(Gtk.StackTransitionType.OVER_LEFT_RIGHT)
        self.main_stack.set_transition_duration(350)

        self.header_ui = Gtk.Builder()
        path = os.path.join(self.ui_dir, "header.ui")
        self.header_ui.add_from_file(path)

        self.header = self.header_ui.get_object("header")
        self.header_overlay = self.header_ui.get_object("header_overlay")
        self.header_nav = self.header_ui.get_object("header_nav")
        self.language_menu_btn = self.header_ui.get_object('language_button')
        self.next_prev_button_box = self.header_ui.get_object('nav_box')

        self.progressbar = self.header_ui.get_object("main_progressbar")
        self.progressbar.set_name('process_progressbar')

        self.logo = self.header_ui.get_object("logo")
        self.logo_text = self.header_ui.get_object("logo_text")
        img_path = os.path.join(self.data_dir, "images", "antergos", "image10.png")
        self.logo.set_from_file(img_path)

        self.header_overlay.add_overlay(self.header)
        self.header_overlay.add_overlay(self.header_nav)
        self.header_overlay.add_overlay(self.progressbar)
        self.header_overlay.set_overlay_pass_through(self.header, True)
        self.header_overlay.set_overlay_pass_through(self.progressbar, True)
        self.header_overlay.set_overlay_pass_through(self.header_nav, True)
        self.set_titlebar(self.header_overlay)

        # To honor our css
        self.header.set_name("header")
        self.logo.set_name("logo")

        self.forward_button = self.header_ui.get_object("forward_button")
        self.backwards_button = self.header_ui.get_object("backwards_button")

        atk_set_image_description(self.forward_button, _("Next step"))
        atk_set_image_description(self.backwards_button, _("Previous step"))

        self.forward_button.set_name('fwd_btn')
        self.forward_button.set_always_show_image(True)

        self.backwards_button.set_name('bk_btn')
        self.backwards_button.set_always_show_image(True)

        # title = "Cnchi {0}".format(info.CNCHI_VERSION)
        title = _("Cnchi Installer")
        self.set_title(title)
        self.logo_text.set_text(title)

        welcome_msg = _("Welcome To Antergos!")
        label = Gtk.Label.new()
        label.set_text(welcome_msg)
        # self.header.set_title(welcome_msg)
        self.header.set_custom_title(label)
        self.header.get_custom_title().get_style_context().add_class('cnchi_title')
        self.header.set_show_close_button(False)

    def pre_load_pages(self):
        # Just load the first two screens (the other ones will be loaded later)
        # We do this to reduce Cnchi's initial startup time.
        self.pages = dict()
        self.pages["location_grp"] = {'title': 'Location',
                                      'prev_page': 'check',
                                      'next_page': 'location',
                                      'pages': ['location', 'timezone', 'keymap']}
        self.pages["check"] = check.Check(self.params, cnchi_main=self)
        self.pages["check"].prepare('forwards', show=False)

        if os.path.exists('/home/antergos/.config/openbox'):
            # In minimal iso, load the system check screen now
            self.current_page = self.pages["check"]
        else:
            self.pages["welcome"] = welcome.Welcome(self.params)
            self.current_page = self.pages["welcome"]

    def load_pages(self):

        top_level_pages = ['check', 'location_grp', 'desktop_grp',
                           'disk_grp', 'user_info', 'summary']

        self.initialize_pages_dict()
        self.stacks.append(self.main_stack)

        diff = 2
        if os.path.exists('/home/antergos/.config/openbox'):
            # In minimal (openbox) we don't have a welcome screen
            diff = 3

        top_pages = [p for p in self.pages if not isinstance(self.pages[p], dict)]
        sub_stacks = [self.pages[p]['pages'] for p in self.pages if p not in top_pages]
        sub_pages = [p for x in sub_stacks for p in x]
        self.all_pages = top_level_pages + sub_pages

        num_pages = len(top_pages) + len(sub_pages)

        self.page_count = num_pages - diff

        if num_pages > 0:
            self.progressbar_step = 1.0 / num_pages

        # self.nav_buttons['backwards_button'] = self.backwards_button
        # self.header_nav.add(self.nav_buttons['backwards_button'])

        for page_name in top_level_pages:
            page = self.pages[page_name]
            if isinstance(page, dict):
                sub_stack = substack.SubStack(params=self.params,
                                              name=page_name,
                                              title=page['title'],
                                              prev_page=page['prev_page'],
                                              next_page=page['next_page'])

                sub_stack.get_style_context().add_class('sub_page')
                sub_stack.set_transition_type(Gtk.StackTransitionType.OVER_DOWN_UP)
                sub_stack.set_transition_duration(350)
                self.sub_nav_btns[page_name] = {
                    'box': Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
                }
                self.sub_nav_btns[page_name]['box'].set_halign(Gtk.Align.CENTER)
                self.sub_nav_btns[page_name]['box'].set_hexpand(True)

                for sub_page_name in page['pages']:
                    sub_page = self.pages[page_name][sub_page_name]
                    if sub_page:
                        sub_page.stack = sub_stack
                        sub_stack.add_titled(sub_page, sub_page_name, sub_page.title)
                        self.sub_nav_btns[page_name][sub_page_name] = Gtk.Button.new_with_label(
                                sub_page.title)
                        self.sub_nav_btns[page_name][sub_page_name].connect(
                                'clicked',
                                self.on_header_nav_button_clicked,
                                {'group_name': page_name, 'name': sub_page_name})
                        sub_page.nav_button = self.sub_nav_btns[page_name][sub_page_name]
                        self.sub_nav_btns[page_name]['box'].add(
                                self.sub_nav_btns[page_name][sub_page_name])
                        sub_page.nav_button_box = self.sub_nav_btns[page_name]['box']

                self.pages[page_name]['group'] = page = sub_stack
                self.stacks.append(sub_stack)

            page.show_all()
            self.main_stack.add_titled(page, page_name, page.title)
            self.nav_buttons[page_name] = Gtk.Button.new_with_label(page.title)
            self.nav_buttons[page_name].connect('clicked',
                                                self.on_header_nav_button_clicked, page_name)

            if isinstance(page, gtkbasebox.GtkBaseBox):
                page.stack = self.main_stack
                page.nav_button = self.nav_buttons[page_name]

            self.header_nav.add(self.nav_buttons[page_name])

        self.nav_buttons['forward_button'] = self.forward_button
        self.header_nav.add(self.nav_buttons['forward_button'])

        self.header_nav.show_all()
        self.current_stack = self.main_stack

    def initialize_pages_dict(self):

        self.pages["location_grp"]["location"] = location.Location(params=self.params)
        if self.pages["location_grp"].get('timezone', False) is False:
            self.pages["location_grp"]["timezone"] = timezone.Timezone(params=self.params,
                                                                       cnchi_main=self)

        self.pages["desktop_grp"] = {'title': 'Desktop Selection',
                                     'prev_page': 'location_grp',
                                     'next_page': 'desktop',
                                     'pages': ['desktop', 'features']}

        if self.settings.get('desktop_ask') or True:
            self.pages["location_grp"]["keymap"] = keymap.Keymap(params=self.params, is_last=True)
            self.pages["desktop_grp"]["desktop"] = desktop.DesktopAsk(params=self.params)
            self.pages["desktop_grp"]["features"] = features.Features(params=self.params,
                                                                      is_last=True)
        else:
            self.pages["location_grp"]["keymap"] = keymap.Keymap(self.params, next_page='features',
                                                                 is_last=True)
            self.pages["desktop_grp"]["features"] = features.Features(self.params,
                                                                      prev_page='location_grp',
                                                                      is_last=True)

        self.pages["disk_grp"] = {'title': 'Disk Setup',
                                  'prev_page': 'desktop_grp',
                                  'next_page': 'installation_ask',
                                  'pages': ['installation_ask', 'installation_automatic',
                                            'installation_alongside', 'installation_advanced',
                                            'installation_zfs']}

        self.pages["disk_grp"]["installation_ask"] = \
            installation_ask.InstallationAsk(params=self.params)

        self.pages["disk_grp"]["installation_automatic"] = \
            installation_automatic.InstallationAutomatic(params=self.params, is_last=True)

        if self.settings.get("enable_alongside"):
            self.pages["disk_grp"]["installation_alongside"] = \
                installation_alongside.InstallationAlongside(params=self.params)
        else:
            self.pages["disk_grp"]["installation_alongside"] = None

        self.pages["disk_grp"]["installation_advanced"] = \
            installation_advanced.InstallationAdvanced(params=self.params, is_last=True)

        self.pages["disk_grp"]["installation_zfs"] = \
            installation_zfs.InstallationZFS(params=self.params, is_last=True)

        self.pages["user_info"] = user_info.UserInfo(params=self.params)
        self.pages["summary"] = summary.Summary(params=self.params)
        self.pages["slides"] = slides.Slides(params=self.params)

    def del_pages(self):
        """ When we get to user_info page we can't go back
        therefore we can delete all previous pages for good """
        # FIXME: As there are more references, this does nothing
        try:
            del self.pages["welcome"]
            del self.pages["language"]
            del self.pages["location"]
            del self.pages["check"]
            del self.pages["desktop"]
            del self.pages["features"]
            del self.pages["keymap"]
            del self.pages["timezone"]
            del self.pages["installation_ask"]
            del self.pages["installation_automatic"]
            if self.pages["installation_alongside"] is not None:
                del self.pages["installation_alongside"]
            del self.pages["installation_advanced"]
            del self.pages["installation_zfs"]
        except KeyError as key_error:
            pass

    def set_geometry(self):
        """ Sets Cnchi window geometry """
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_resizable(False)
        self.set_size_request(self._main_window_width, self._main_window_height)
        self.set_default_size(self._main_window_width, self._main_window_height)

        geom = Gdk.Geometry()
        geom.min_width = self._main_window_width
        geom.min_height = self._main_window_height
        geom.max_width = self._main_window_width
        geom.max_height = self._main_window_height
        geom.base_width = self._main_window_width
        geom.base_height = self._main_window_height
        geom.width_inc = 0
        geom.height_inc = 0

        hints = (Gdk.WindowHints.MIN_SIZE |
                 Gdk.WindowHints.MAX_SIZE |
                 Gdk.WindowHints.BASE_SIZE |
                 Gdk.WindowHints.RESIZE_INC)

        self.set_geometry_hints(None, geom, hints)

    def on_key_release(self, widget, event, data=None):
        """ Params: GtkWidget *widget, GdkEventKey *event, gpointer data """
        if event.keyval == Gdk.keyval_from_name('Escape'):
            response = self.confirm_quitting()
            if response == Gtk.ResponseType.YES:
                self.on_exit_button_clicked(self)
                self.destroy()

    def confirm_quitting(self):
        message = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            destroy_with_parent=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=_("Do you really want to quit the installer?"))
        response = message.run()
        message.destroy()
        return response

    def on_exit_button_clicked(self, widget, data=None):
        """ Quit Cnchi """
        try:
            misc.remove_temp_files()
            logging.info("Quiting installer...")
            for proc in self.process_list:
                if proc.is_alive():
                    proc.terminate()
                    proc.join()
            logging.shutdown()
        except KeyboardInterrupt:
            pass

    def on_language_button_clicked(self, widget, data=None):
        self.popover.show_all()
        self.language_widget.popover_is_visible = True

    def on_language_popover_closed(self, widget, data=None):
        self.language_widget.popover_is_visible = False

    def on_header_nav_button_clicked(self, widget, data=None):
        logging.debug(data)
        page_name = data if not isinstance(data, dict) else data.get('name', False)
        curr_page = self.current_page.name
        curr_stack = self.current_stack.name if hasattr(self.current_stack, 'name') else None
        page = stack = None

        for stack in self.stacks:
            try:
                page = stack.get_child_by_name(page_name)
            except Exception as err:
                logging.debug(err)
            if page:
                break
        else:
            logging.debug('unable to find page in stacks')

        if (not page or not page.can_show) and page_name != self.current_page.next_page:
            return

        stored = self.current_page.store_values()

        if not stored:
            logging.warning('stored is required: %s', stored)
            return

        prev_page = self.current_page
        if 'welcome' != prev_page.name:
            self.header.set_title('')
            prev_page.nav_button.set_state_flags(Gtk.StateFlags.NORMAL, True)
        else:
            self.main_box.set_visible(False)
            self.main_stack.set_visible(True)
            self.current_stack = self.main_stack
            self.current_stack.can_show = True
            self.current_stack.show_all()

        self.set_progressbar_step(self.progressbar_step)
        self.current_page = page

        if not isinstance(self.current_page, gtkbasebox.GtkBaseBox):
            self.main_stack.show_all()
            self.main_stack.set_visible_child_name(page_name)
            self.current_stack = self.pages[page_name]['group']
            self.current_stack.can_show = True

            if self.nav_buttons.get('selected', False):
                self.nav_buttons['selected'].set_state_flags(Gtk.StateFlags.NORMAL, True)
            self.nav_buttons[page_name].set_state_flags(Gtk.StateFlags.SELECTED, True)
            self.nav_buttons['selected'] = self.nav_buttons[page_name]

            # The sub-stack container itself is not a page. Let's proceed to
            # first page within the sub-stack (a non-top-level page)
            self.current_page = self.pages[page_name][self.current_stack.next_page]

            self.prepare_sub_nav_buttons({'name': self.current_page.name,
                                          'group': page_name,
                                          'previous': self.current_page.prev_page,
                                          'nav_button': self.current_page.nav_button,
                                          'nav_button_box': self.current_page.nav_button_box})
        else:
            self.current_stack = stack

        self.current_page.prepare('forwards')
        self.current_page.can_show = True
        self.current_stack.set_visible_child_name(self.current_page.name)
        self.current_page.nav_button.set_state_flags(Gtk.StateFlags.SELECTED, True)
        self.header.set_subtitle('')

    def set_progressbar_step(self, add_value):
        new_value = self.progressbar.get_fraction() + add_value
        if new_value > 1:
            new_value = 1
        if new_value < 0:
            new_value = 0
        self.progressbar.set_fraction(new_value)
        if new_value > 0:
            self.progressbar.show()
        else:
            self.progressbar.hide()

    def prepare_sub_nav_buttons(self, data):
        page = data['name']
        group = data['group']
        prev = data.get('previous', None)
        curr_btns = self.sub_nav_box.get_children()
        noop = False
        logging.debug('curr_btns is: %s ', curr_btns)
        if curr_btns and curr_btns[0] is not data['nav_button_box']:
            logging.debug('curr_btns is not btn_container')
            self.sub_nav_box.remove(curr_btns[0])
        elif curr_btns and curr_btns[0] is data['nav_button_box']:
            logging.debug('curr_btns is btn_container')
            noop = True

        if not noop:
            self.sub_nav_box.add(data['nav_button_box'])

        self.sub_nav_box.height_request = 48
        self.sub_nav_box.show_all()

    def on_has_internet_connection(self):
        if self.cnchi_started:
            if self.pages["location_grp"].get('timezone', False) is False:
                self.pages["location_grp"]["timezone"] = timezone.Timezone(params=self.params,
                                                                           cnchi_main=self)
                time.sleep(1)
                self.pages['location_grp']['timezone'].prepare(show=False)
        else:
            self.timezone_start_needed = True

    def on_timezone_set(self):
        logging.debug('TIMEZONE SET')

    def on_forward_button_clicked(self, widget, data=None):
        """ Show next screen """

        curr_page_name = self.current_page.name
        next_page_name = self.current_page.get_next_page()

        if not next_page_name:
            logging.warning('next_page_name required: %s', next_page_name)
            return

        if not self.pages_loaded:
            # Load all pages
            self.load_pages()
            self.pages_loaded = True
            self.progressbar_step = 1.0 / self.page_count

        return self.on_header_nav_button_clicked(widget, next_page_name)

        stored = self.current_page.store_values()

        if not stored:
            logging.warning('stored is required: %s', stored)
            return

        self.set_progressbar_step(self.progressbar_step)

        if 'welcome' == self.current_page.name:
            self.main_box.set_visible(False)
            self.main_stack.set_visible(True)
            self.current_stack = self.main_stack
            self.current_stack.can_show = True

        if self.current_page.in_group and not self.current_page.is_last:
            # Set state of the soon-to-be non-active page's tab to normal (for css bg color)
            self.sub_nav_btns[self.current_grp][curr_page_name].set_state_flags(
                    Gtk.StateFlags.NORMAL, True)
            # We are currently viewing a non-top-level page (in a sub-stack)
            self.current_page = self.pages[self.current_grp][next_page_name]
        else:
            # We are viewing a top-level page or a sub-stack container
            if 'welcome' != self.current_page.name:
                try:
                    self.nav_buttons[self.current_page.name].set_state_flags(
                            Gtk.StateFlags.NORMAL, True)
                except KeyError:
                    self.nav_buttons[self.current_grp].set_state_flags(
                            Gtk.StateFlags.NORMAL, True)
            self.current_page = self.pages[next_page_name]

        if not self.current_page:
            logging.warning('current_page is required: %s', self.current_page)
            return

        if isinstance(self.current_page, dict):
            # We are viewing a sub-stack container
            self.main_stack.set_visible_child_name(next_page_name)

            # Set the current_grp attr so we know we are in a sub-stack next time.
            self.current_grp = next_page_name
            self.current_stack = self.pages[next_page_name]['group']
            self.current_stack.can_show = True

            # The sub-stack container itself is not a page. Let's proceed to
            # first page within the sub-stack (a non-top-level page)
            page_after_next = self.current_stack.next_page
            self.current_page = self.pages[self.current_grp][page_after_next]

            # Set the state of our nav buttons so they appear properly highlighted
            self.nav_buttons[self.current_grp].set_state_flags(
                    Gtk.StateFlags.SELECTED, True)
            self.prepare_sub_nav_buttons({'group': self.current_grp,
                                          'name': self.current_page.name,
                                          'prev': self.current_page.prev_page})

        elif self.current_page.in_group:
            # We are viewing a non-top-level page (inside sub-stack container)
            self.sub_nav_btns[self.current_grp][next_page_name].set_state_flags(
                    Gtk.StateFlags.SELECTED, True)

        elif not self.current_page.in_group:
            self.current_stack = self.main_stack
            self.current_grp = None
            self.nav_buttons[self.current_page.name].set_state_flags(Gtk.StateFlags.SELECTED, True)

        self.current_page.prepare('forwards')
        self.current_page.can_show = True
        self.current_stack.set_visible_child_name(self.current_page.name)
        self.header.set_subtitle('')

        if self.current_page.prev_page:
            # There is a previous page, show back button
            self.backwards_button.show()
            self.backwards_button.set_sensitive(True)
        else:
            # We can't go back, hide back button
            self.backwards_button.hide()
            if self.current_page == "slides":
                # Show logo in slides screen
                self.logo.show_all()

    def on_backwards_button_clicked(self, widget, data=None):
        """ Show previous screen """
        prev_page = self.current_page.get_prev_page()

        if prev_page is not None:
            self.set_progressbar_step(-self.progressbar_step)

            # If we go backwards, don't store user changes
            # self.current_page.store_values()

            if 'welcome' == prev_page:
                self.main_stack.set_visible(False)
                self.main_box.set_visible(True)

            self.current_page = self.pages[prev_page]

            if self.current_page is not None:
                self.current_page.prepare('backwards')

                if self.current_page.get_prev_page() is None:
                    # We're at the first page
                    self.backwards_button.hide()
                    self.progressbar.hide()
                    self.logo.show_all()
                else:
                    self.main_stack.set_visible_child_name(self.current_page.get_name())
