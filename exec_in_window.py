import sublime, sublime_plugin
import os, sys
import subprocess
import functools
import time
import os
import tempfile
import codecs
from os import path
from os.path import isfile
execplugin = __import__("Default.exec")
execcmd = execplugin.exec

class BuildFileNotFound(Exception):
    pass

class ExecInWindowAppendCommand(sublime_plugin.TextCommand):
    def run(self, edit, **kwargs):
        data = kwargs.get( 'data', None )
        if data:
            self.view.insert( edit, self.view.size(), data )

class ExecInWindowClearViewCommand(sublime_plugin.TextCommand):
    def run(self, edit, user_input=None, *args):
        self.view.erase(edit, sublime.Region(0, self.view.size()))

class ExecInWindowCommand(execcmd.sublime_plugin.WindowCommand, execcmd.ProcessListener):
    def run(self, cmd = [], file_regex = "", line_regex = "", working_dir = "", encoding = "utf-8", env = {}, quiet = False, kill = False, dm_maker = False, dm_daemon = False, dm_seeker = False, **kwargs):

        if dm_maker:
            cmd = [self.get_setting("installation_path") + self.get_setting("compiler_executable")]

        if dm_daemon:
            cmd = [self.get_setting("installation_path") + self.get_setting("daemon_executable")] + ["dmbstub"] + ["-trusted"]

        if dm_seeker:
            cmd = [self.get_setting("installation_path") + self.get_setting("seeker_executable")] + ["dmbstub"] + ["-trusted"]

        if kill:
            if self.proc:
                self.proc.kill()
                self.proc = None
                self.append_data(None, "[Cancelled]")
            return

        self.post_command=env.get('post_command',None)

        # Create temporary file if doesn't exists
        if self.window.active_view().file_name():
            self.file = self.window.active_view().file_name()
        else:
            self.file          = self.create_temp_file()
            cmd[cmd.index('')] = self.file

        try:
            buildFileDetails = self.getBuildFileDetails(os.path.dirname(self.file), ".dmb" if (dm_daemon or dm_seeker) else ".dme")
        except Exception as e:
            self.append_data(None, str(e) + "\n")
            return
        
        working_dir = buildFileDetails[1]

        if dm_daemon or dm_seeker:
            cmd[1] = buildFileDetails[0]
        else:
            cmd += [buildFileDetails[0]]

        self.output_view = self.window.open_file(working_dir + "/Build System.dm")
        self.output_view.set_scratch(True)
        self.output_view.set_read_only(False)

        self.output_view.settings().set("result_file_regex", file_regex)
        self.output_view.settings().set("result_line_regex", line_regex)
        self.output_view.settings().set("result_base_dir", working_dir)

        self.encoding = encoding
        self.quiet = quiet
        self.proc = None
        self.clear_view()

        if not self.quiet:
            execcmd.sublime.status_message("Building")

        merged_env = env.copy()
        if self.window.active_view():
            user_env = self.window.active_view().settings().get('build_env')
            if user_env:
                merged_env.update(user_env)

        # Change to the working dir, rather than spawning the process with it,
        # so that emitted working dir relative path names make sense
        if working_dir != "":
            os.chdir(working_dir)

        err_type = OSError
        if os.name == "nt":
            err_type = WindowsError

        try:
            # Forward kwargs to AsyncProcess
            self.proc = execcmd.AsyncProcess(cmd, False, merged_env, self, **kwargs)
        except err_type as e:
            self.append_data(None, str(e) + "\n")
            self.append_data(None, "[cmd:  " + str(cmd) + "]\n")
            self.append_data(None, "[dir:  " + str(os.getcwd()) + "]\n")
            if "PATH" in merged_env:
                self.append_data(None, "[path: " + str(merged_env["PATH"]) + "]\n")
            else:
                self.append_data(None, "[path: " + str(os.environ["PATH"]) + "]\n")
            if not self.quiet:
                self.append_data(None, "[Finished]")

    def is_enabled(self, kill = False):
        if kill:
            return hasattr(self, 'proc') and self.proc and self.proc.poll()
        else:
            return True

    def clear_view(self):
        self.output_view.run_command('exec_in_window_clear_view',{})

    def create_temp_file(self):
        view = self.window.active_view()
        region = execcmd.sublime.Region(0, view.size())
        content = view.substr(region)

        filename = '%s.tmp' % view.id()
        path = os.path.join(tempfile.gettempdir(), filename)
        file = open(path, 'w')
        file.write(str(content.encode('utf-8')))
        file.close()
        return path

    def append_data(self, proc, data):
        if proc != self.proc:
            # a second call to exec has been made before the first one
            # finished, ignore it instead of intermingling the output.
            if proc:
                proc.kill()
            return
        try:
            if isinstance( data, str ):
                data = data.encode( self.encoding )
            string = data.decode(self.encoding)
        except Exception as e:
            string = "[Decode error - output not " + self.encoding + "]\n"
            proc = None

        # Normalize newlines, Sublime Text always uses a single \n separator
        # in memory.
        string = string.replace('\r\n', '\n').replace('\r', '\n')

        selection_was_at_end = (len(self.output_view.sel()) == 1
            and self.output_view.sel()[0]
                == execcmd.sublime.Region(self.output_view.size()))

        self.output_view.run_command('exec_in_window_append',{ 'data': string })
        if selection_was_at_end:
            self.output_view.show(self.output_view.size())

    def finish(self, proc):
        if not self.quiet:
            elapsed = time.time() - proc.start_time
            exit_code = proc.exit_code()
            if exit_code == 0 or exit_code == None:
                self.append_data(proc, ("\n[Finished in %.1fs]\n") % (elapsed))
                if self.post_command:
                    self.append_data(proc, ("\n[Post Command:%s]\n") % (self.post_command))
                    sublime.active_window().run_command(self.post_command)
            else:
                self.append_data(proc, ("\n[Finished in %.1fs with exit code %d]\n") % (elapsed, exit_code))

        if proc != self.proc:
            return

        errs = self.output_view.find_all_results()

        if len(errs) == 0:
            execcmd.sublime.status_message("Build finished")
        else:
            execcmd.sublime.status_message(("Build finished with %d errors") % len(errs))


    def on_data(self, proc, data):
        execcmd.sublime.set_timeout(functools.partial(self.append_data, proc, data), 0)

    def on_finished(self, proc):
        execcmd.sublime.set_timeout(functools.partial(self.finish, proc), 0)

    def get_setting(self, config):

        settings = sublime.load_settings('Preferences.sublime-settings')

        if settings.get('dm_' + config):
            return settings.get('dm_' + config)
        else:
            settings = sublime.load_settings('DM.sublime-settings')
            return settings.get('dm_' + config)

    def walk_up(self, bottom):
        """
        mimic os.walk, but walk 'up'
        instead of down the directory tree
        """

        bottom = path.realpath(bottom)

        #get files in current dir
        try:
            names = os.listdir(bottom)
        except Exception as e:
            print(e)
            return


        dirs, nondirs = [], []
        for name in names:
            if path.isdir(path.join(bottom, name)):
                dirs.append(name)
            else:
                nondirs.append(name)

        yield bottom, dirs, nondirs

        new_path = path.realpath(path.join(bottom, '..'))

        # see if we are at the top
        if new_path == bottom:
            return

        for x in self.walk_up(new_path):
            yield x

    def getBuildFileDetails(self, startDir, extension):
        for dirName, subdirList, fileList in self.walk_up(startDir):
            for file in fileList:
                if file.lower().endswith(extension):
                    return file, dirName
        
        raise BuildFileNotFound("Unable to find build file with extension: " + extension)
