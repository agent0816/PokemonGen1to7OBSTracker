import argparse
import ast
import logging
import os
import subprocess
import tempfile
logging.basicConfig(level=logging.DEBUG, filename='logs/updater.log',format='[%(asctime)s] %(levelname)s: %(message)s', filemode='w', encoding='utf-8')

log = logging.getLogger(__name__)

def _win_overwrite(**kwargs):
    extracted_files = kwargs.get("extracted_files")
    source_path = kwargs.get("source_path")
    app_destination = kwargs.get("app_destination")
    _win_overwrite_ps(extracted_files, source_path, app_destination)

def _win_overwrite_ps(extracted_files, source_path, app_destination):
    current_pid = os.getpid()

    powershell_script = 'Set-ExecutionPolicy Unrestricted -Scope Process -Force \n'
    powershell_script += f"while (Get-Process -Id {current_pid} -ErrorAction SilentlyContinue) {{Start-Sleep -Seconds 1}}"
    extracted_paths = {}

    for path in extracted_files:
        test_directory = os.path.abspath(os.path.join(source_path, path))
        if os.path.isdir(test_directory):
            directory = os.path.abspath(os.path.join(app_destination, path))
            if not os.path.exists(directory):
                powershell_script += f"New-Item -Path \"{directory}\" -ItemType directory \n"
        else:
            extracted_paths[os.path.abspath(os.path.join(source_path, path))] = os.path.abspath(os.path.join(app_destination, os.path.dirname(path)))

    for source_file, destination_file in extracted_paths.items():
        powershell_script += f"Move-Item -Path \"{source_file}\" -Destination \"{destination_file}\" -Force \n"

    with tempfile.NamedTemporaryFile(delete=False, suffix='.ps1', mode='w', encoding='utf-8') as f:
        f.write(powershell_script)
        temp_script_path = f.name

    log.info(powershell_script)

    powershell_script = f"Remove-Item -Path {os.path.join(source_path, 'backend')} -Recurse -Force\n"
    powershell_script += f"Unregister-ScheduledTask -TaskName 'UpdatePokemonOBSTracker' -Confirm:$false\n"
    powershell_script += f"Remove-Item -Path {temp_script_path} -Force"

    log.info(powershell_script)

    with open(temp_script_path, 'a', encoding='utf-8') as f:
        f.write(powershell_script)

    print(temp_script_path)

    # Erstellen Sie den Befehl, um das PowerShell-Skript mit Admin-Rechten auszuführen
    command = f'''
$Action = New-ScheduledTaskAction -Execute 'Powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File {temp_script_path}"
$Trigger = New-ScheduledTaskTrigger -At (Get-Date).AddSeconds(5) -Once
Register-ScheduledTask -Action $Action -Trigger $Trigger -TaskName "UpdatePokemonOBSTracker" -User "NT AUTHORITY\\SYSTEM" -RunLevel Highest
            '''
    
    # command = f"Start-Process -Verb RunAs -Wait -FilePath PowerShell -ArgumentList '-NoProfile -ExecutionPolicy Bypass -Command {powershell_script}'"
    log.info(command)
    # Führen Sie den Befehl aus
    process = subprocess.Popen(["powershell", "-Command", command], stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
    stdout, stderr = process.communicate()

    log.info("out: ", stdout.decode('windows-1252'))
    log.info("error: ", stderr.decode('windows-1252'))

def parsing_arguments():
    log.info("parsing_arguments")
    parser = argparse.ArgumentParser(description='Update tool for MyApp.')
    parser.add_argument('--app-path', required=True, help='Path to the main application.')
    parser.add_argument('--update-path', required=True, help='Path to the updates.')
    parser.add_argument('--extracted-files', required=True, help='Path to the updates.')

    args = parser.parse_args()

    log.info(f"{args=}")

    return args

def main():
    try:
        args = parsing_arguments()

        update_path = args.update_path
        app_path = args.app_path
        extracted_files = ast.literal_eval(args.extracted_files)

        log.info(f"{update_path=}")
        log.info(f"{app_path=}")
        log.info(f"{extracted_files=}")

        _win_overwrite(extracted_files=extracted_files, source_path=update_path, app_destination=app_path)

    except Exception as error:
        log.error(error, exc_info=True)

main()