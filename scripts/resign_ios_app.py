#!/usr/bin/env python3
from __future__ import annotations
import argparse, os, plistlib, shutil, subprocess, tempfile
from pathlib import Path


def run(cmd, check=True):
    print('+', ' '.join(str(c) for c in cmd))
    p=subprocess.run([str(c) for c in cmd], text=True, capture_output=True)
    if p.stdout: print(p.stdout, end='')
    if p.stderr: print(p.stderr, end='')
    if check and p.returncode!=0:
        raise SystemExit(p.returncode)
    return p


def profile_plist(profile: Path):
    raw=subprocess.check_output(['security','cms','-D','-i',str(profile)])
    return plistlib.loads(raw)


def write_entitlements(profile: Path, app_id: str, out: Path):
    pl=profile_plist(profile)
    ent=dict(pl.get('Entitlements', {}))
    prefix=(pl.get('ApplicationIdentifierPrefix') or [None])[0]
    team=(pl.get('TeamIdentifier') or [prefix])[0]
    if prefix:
        ent['application-identifier']=f'{prefix}.{app_id}'
    if team:
        ent['com.apple.developer.team-identifier']=team
    # remove beta reports active if present and not supported by dev signing
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('wb') as f:
        plistlib.dump(ent, f)
    return ent


def bundle_id(bundle: Path):
    with (bundle/'Info.plist').open('rb') as f:
        return plistlib.load(f).get('CFBundleIdentifier')


def copy_profile(bundle: Path, profile: Path):
    shutil.copy2(profile, bundle/'embedded.mobileprovision')


def sign_bundle(bundle: Path, identity: str, ent: Path | None = None):
    # sign nested dylibs/framework-like bundles first inside this bundle, shallow to avoid duplicate with app-level order
    cmd=['codesign','--force','--timestamp=none','--sign',identity]
    if ent:
        cmd += ['--entitlements', str(ent)]
    cmd += [str(bundle)]
    run(cmd)
    run(['codesign','--verify','--deep','--strict','--verbose=2',str(bundle)])


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--src-app', required=True)
    ap.add_argument('--out-app', required=True)
    ap.add_argument('--identity', required=True)
    ap.add_argument('--main-profile', required=True)
    ap.add_argument('--profile', action='append', required=True, metavar='BUNDLE_ID=PROFILE', help='Provisioning profile mapping; repeat for app/extensions')
    args=ap.parse_args()
    src=Path(args.src_app); out=Path(args.out_app)
    if out.exists(): shutil.rmtree(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, out, symlinks=True)
    profs={}
    for item in args.profile:
        if '=' not in item:
            raise SystemExit('--profile must be BUNDLE_ID=PROFILE')
        bid, profile = item.split('=', 1)
        profs[bid] = Path(profile)
    work=out.parent/'resign-work'
    if work.exists(): shutil.rmtree(work)
    work.mkdir(parents=True)

    # collect nested code. Sign deeper first: frameworks/dylibs/bundles inside app, then appex, then app.
    nested=[]
    for p in out.rglob('*'):
        if p.is_dir() and p.suffix in ('.framework','.appex'):
            nested.append(p)
        elif p.is_file() and (p.suffix=='.dylib' or os.access(p, os.X_OK)):
            # don't blindly sign every executable in root; codesign will ignore non-Mach-O with error, so filter via file
            try:
                desc=subprocess.check_output(['file',str(p)], text=True, stderr=subprocess.DEVNULL)
            except Exception:
                continue
            if 'Mach-O' in desc:
                nested.append(p)
    # unique deepest first, but exclude main app path
    seen=[]
    for p in sorted(nested, key=lambda x: len(x.parts), reverse=True):
        if p == out: continue
        if any(str(p).startswith(str(s) + '/') for s in seen):
            # still sign contained Mach-O before parent; if parent already in seen this won't happen due deepest sort
            pass
        if p not in seen: seen.append(p)

    # Copy profiles and prepare entitlements for appex/main bundles.
    bundles = [p for p in out.rglob('*.appex') if p.is_dir()]
    bundles.append(out)
    for b in bundles:
        bid=bundle_id(b)
        pr=profs.get(bid)
        if not pr: raise SystemExit(f'no profile for {bid}')
        copy_profile(b, pr)
        write_entitlements(pr, bid, work/f'{bid}.entitlements.plist')
        print('profile', bid, pr)

    # Sign all nested non-appex frameworks/dylibs/bundles with no entitlements first.
    for p in seen:
        if p.suffix == '.appex':
            continue
        # skip directories under appex; they will be signed before appex if present, ok not skip
        sign_bundle(p, args.identity, None)

    # Sign appex with own entitlements
    for appex in bundles[:-1]:
        sign_bundle(appex, args.identity, work/f'{bundle_id(appex)}.entitlements.plist')

    # Sign app last
    sign_bundle(out, args.identity, work/f'{bundle_id(out)}.entitlements.plist')
    print('RESIGNED_APP=', out)

if __name__=='__main__': main()
