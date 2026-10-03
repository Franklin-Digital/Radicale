// calendar-publisher — publishes the Earnings, Economic Indicator and SMB Sessions calendars
// to Radicale (calendar.franklinfinancial.ai). DAYTRADE-778.
//
// LOADED, not run directly. The Jenkins job definition is a small bootstrap
// (franklin/deploy/jenkins/config.xml) that syncs /home/Franklin/franklin-radicale
// to origin/franklin_1.0prod and then `load`s this file, so the pipeline logic
// lives in git, not in Jenkins XML. There is no GitHub deploy key for the
// Radicale fork, which is why this is not a CpsScmFlowDefinition like the
// franklin-infra jobs.
//
// Jenkins is the ONLY scheduler for this job. A systemd timer was removed in
// its favour: disableConcurrentBuilds() cannot see a systemd run, so two
// schedulers would race on the same calendars.
//
// Parameters (declared in config.xml so they exist before the first build):
//   DRY_RUN  compute and log new/changed/stale, write nothing
//   FORCE    re-PUT every event even if unchanged (~10 min)
//   ONLY     both | earnings | economic   ('both' = every calendar, SMB included)

// Python environment: the host convention /home/Franklin/venvs/<component>-<env>.
// Built from franklin/deploy/requirements-franklin-radicale-prod.txt with NO
// editable install of this repo: commands run with cwd = the checkout, so
// `python -m pytest` imports the checked-out radicale package directly.
@groovy.transform.Field
String VENV = '/home/Franklin/venvs/franklin-radicale-prod'

// SMB Sessions (2026-10-03). rt.smbtraining.com's cookie lives ONLY on the DGX
// (Sal, 2026-09-22), so the fetch runs there, with the DGX's own venv of the
// same name. Only the sanitised JSON (no join links) comes back to mac-pro.
@groovy.transform.Field
String SMB_JSON = '/tmp/calendar-publisher-smb-meetings.json'

// Run a command as sal, in the repo, with franklin.env loaded. franklin.env
// has no `export` lines, so `set -a` is required or every variable is empty.
// `cmd` must not contain single quotes. With `log`, output is also tee'd in
// Jenkins' own shell into the workspace so the build can read it back.
def asSal(String cmd, String log = null) {
    def tail = log ? " 2>&1 | tee ${log}" : ""
    sh """#!/bin/bash
set -euo pipefail
sudo -u sal -H bash -c 'set -euo pipefail; set -a; . /home/Franklin/secrets/franklin.env; set +a; cd /home/Franklin/franklin-radicale; ${cmd}'${tail}
"""
}

@NonCPS
def summarise(String out) {
    def counts = []
    int skipped = 0
    for (String line : out.split('\n')) {
        if (line.contains(' source | ')) { counts << line.replaceFirst(/^INFO /, '') }
        if (line.contains('skipping ')) { skipped++ }
    }
    return [counts.join('<br>'), skipped]
}

def run(params) {
    timeout(time: 45, unit: 'MINUTES') {

        stage('Preflight') {
            // Each check names a failure that would otherwise look like
            // something else: Radicale down -> every PUT fails; Postgres down
            // -> query error; empty password -> 401s that read as a rights bug.
            asSal('code=$(curl -s -o /dev/null -w "%{http_code}" -X PROPFIND http://127.0.0.1:5232/ || true); echo "radicale unauthenticated PROPFIND -> $code (expect 401)"; [ "$code" = 401 ] || { echo "FAIL: Radicale not serving on 127.0.0.1:5232"; exit 1; }; docker exec postgres-prod pg_isready -h 127.0.0.1 -q && echo "postgres-prod: ready"; [ -n "${CALENDAR_PUBLISHER_PASSWORD:-}" ] || { echo "FAIL: CALENDAR_PUBLISHER_PASSWORD empty"; exit 1; }; [ -n "${BENNY_PASSWORD:-}" ] || { echo "FAIL: BENNY_PASSWORD empty"; exit 1; }; echo "secrets: present"')
        }

        stage('Tests') {
            // ~2s. Gates every run: the publisher shipped through four bugs
            // that each looked fine until run against the real server.
            asSal("${VENV}/bin/python -m pytest franklin/publisher franklin/tests -q -p no:cacheprovider")
        }

        stage('Fetch SMB sessions (dgx)') {
            // The script comes from THIS checkout (git), copied out via sal the
            // same way the bootstrap copies this file (jenkins has no read access
            // to the checkout), then runs on the DGX, where the rt cookie lives;
            // the dgx agent has no clone of this fork. A failure here never blocks
            // the other calendars: publish then leaves the SMB calendar untouched.
            sh "sudo -u sal -H cat /home/Franklin/franklin-radicale/franklin/publisher/fetch_smb_meetings.py > fetch_smb_meetings.py"
            def fetcher = readFile('fetch_smb_meetings.py')
            def fetched = false
            try {
                // Bounded: a busy dgx agent must not hold the hourly run for 45 min.
                timeout(time: 10, unit: 'MINUTES') {
                    node('dgx') {
                        writeFile file: 'fetch_smb_meetings.py', text: fetcher
                        def rc = sh(returnStatus: true, script: '''#!/bin/bash
set -uo pipefail
/home/Franklin/venvs/franklin-radicale-prod/bin/python fetch_smb_meetings.py > smb-meetings.json
''')
                        if (rc == 0) {
                            stash name: 'smb-meetings', includes: 'smb-meetings.json'
                            fetched = true
                        } else if (rc == 3) {
                            unstable('SMB calendar NOT refreshed: rt.smbtraining.com cookie rejected. Refresh it: smb-cookie smb')
                        } else {
                            unstable("SMB calendar NOT refreshed: fetch failed (rc=${rc})")
                        }
                    }
                }
            } catch (e) {
                unstable("SMB calendar NOT refreshed: dgx fetch did not complete (${e})")
            }
            if (fetched) {
                unstash 'smb-meetings'
                sh "install -m 644 smb-meetings.json ${SMB_JSON}"
            } else {
                sh "rm -f ${SMB_JSON}"
            }
        }

        stage('Publish') {
            def args = []
            if (params.DRY_RUN) { args << '--dry-run' }
            if (params.FORCE)   { args << '--force' }
            if (params.ONLY && params.ONLY != 'both') { args << "--only ${params.ONLY}" }
            if (fileExists(SMB_JSON)) { args << "--smb-json ${SMB_JSON}" }
            def argStr = args.join(' ')
            echo "publish_calendars.py ${argStr ?: '(no flags)'}"

            asSal("${VENV}/bin/python franklin/publisher/publish_calendars.py ${argStr}", 'publish.log')

            def res = summarise(readFile('publish.log'))
            currentBuild.description = (params.DRY_RUN ? 'DRY RUN<br>' : '') + res[0]
            // A row the publisher cannot render is skipped rather than fatal, so
            // one bad vendor row cannot blank a calendar. It must still be LOUD.
            if (res[1] > 0) {
                unstable("${res[1]} source row(s) skipped - see WARNING lines")
            }
        }
    }
}

return this
