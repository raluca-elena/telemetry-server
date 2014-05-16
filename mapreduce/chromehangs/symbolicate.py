#!/usr/bin/env python
# encoding: utf-8
"""
symbolicate.py
Copyright (c) 2012 Mozilla Foundation. All rights reserved.
"""

import sys
import getopt
import json
import sys
import urllib2
import re
import gzip
from multiprocessing import Pool
import multiprocessing

help_message = '''
    Takes chrome hangs list of memory addresses from JSON dumps and converts them to stack traces
    Required:
        -i, --input <input_file>
        -o, --output <output_file>
        -d, --date yyyymmdd
    Optional:
        -h, --help
'''

SYMBOL_SERVER_URL = "http://symbolapi.mozilla.org:80/"

# We won't bother symbolicating file+offset pairs that occur less than
# this many times.
MIN_HITS = 0

MIN_FRAMES = 15
irrelevantSignatureRegEx = re.compile('|'.join([
  'mozilla::ipc::RPCChannel::Call',
  '@-*0x[0-9a-fA-F]{2,}',
  '@-*0x[1-9a-fA-F]',
  'ashmem',
  'app_process@0x.*',
  'core\.odex@0x.*',
  '_CxxThrowException',
  'dalvik-heap',
  'dalvik-jit-code-cache',
  'dalvik-LinearAlloc',
  'dalvik-mark-stack',
  'data@app@org\.mozilla\.fennec-\d\.apk@classes\.dex@0x.*',
  'framework\.odex@0x.*',
  'google_breakpad::ExceptionHandler::HandleInvalidParameter.*',
  'KiFastSystemCallRet',
  'libandroid_runtime\.so@0x.*',
  'libbinder\.so@0x.*',
  'libc\.so@.*',
  'libc-2\.5\.so@.*',
  'libEGL\.so@.*',
  'libdvm\.so\s*@\s*0x.*',
  'libgui\.so@0x.*',
  'libicudata.so@.*',
  'libMali\.so@0x.*',
  'libutils\.so@0x.*',
  'libz\.so@0x.*',
  'linux-gate\.so@0x.*',
  'mnt@asec@org\.mozilla\.fennec-\d@pkg\.apk@classes\.dex@0x.*',
  'MOZ_Assert',
  'MOZ_Crash',
  'mozcrt19.dll@0x.*',
  'mozilla::ipc::RPCChannel::Call\(',
  '_NSRaiseError',
  '(Nt|Zw)?WaitForSingleObject(Ex)?',
  '(Nt|Zw)?WaitForMultipleObjects(Ex)?',
  'nvmap@0x.*',
  'org\.mozilla\.fennec-\d\.apk@0x.*',
  'RaiseException',
  'RtlpAdjustHeapLookasideDepth',
  'system@framework@*\.jar@classes\.dex@0x.*',
  '___TERMINATING_DUE_TO_UNCAUGHT_EXCEPTION___',
  'WaitForSingleObjectExImplementation',
  'WaitForMultipleObjectsExImplementation',
  'RealMsgWaitFor.*'
  '_ZdlPv',
  'zero'
]))
rawAddressRegEx = re.compile("-*0x[0-9a-fA-F]{1,}")
jsFrameRegEx = re.compile("^js::")
interestingLibs = ["xul.dll", "firefox.exe", "mozjs.dll"]
boringEventHandlingFrames = set([
  "NS_ProcessNextEvent_P(nsIThread *,bool) (in xul.pdb)",
  "mozilla::ipc::MessagePump::Run(base::MessagePump::Delegate *) (in xul.pdb)",
  "MessageLoop::RunHandler() (in xul.pdb)",
  "MessageLoop::Run() (in xul.pdb)",
  "nsBaseAppShell::Run() (in xul.pdb)",
  "nsAppShell::Run() (in xul.pdb)",
  "nsAppStartup::Run() (in xul.pdb)",
  "XREMain::XRE_mainRun() (in xul.pdb)",
  "XREMain::XRE_main(int,char * * const,nsXREAppData const *) (in xul.pdb)"
])

# Pulled this method from Vladan's code
def symbolicate(combined_stacks):
    print "About to symbolicate", len(combined_stacks.keys()), "libs"
    # TODO: batch small ones, split up large ones.
    # combined_stacks is
    #  {
    #    (lib1, debugid1): [offset11, offset12, ...]
    #    (lib2, debugid2): [offset21, offset22, ...]
    #    ...
    #  }
    longest = 0
    for k, v in combined_stacks.iteritems():
        print "Request had", len(v), "items"
        if len(v) > longest:
            longest = len(v)
        lib, debugid = k
        memoryMap = [[lib, debugid]]
        stacks = [[ [0, offset] for offset in v ]]
        requestObj = {"stacks": stacks, "memoryMap": memoryMap, "version": 3}
        requestJson = min_json(requestObj)
        print requestJson

    print "longest set of offsets contained", longest, "items"

    return {}
    # TODO

    if isinstance(chromeHangsObj, list):
        version = 1
        requestObj = chromeHangsObj
        numStacks = len(chromeHangsObj)
        if numStacks == 0:
            return []
    else:
        numStacks = len(chromeHangsObj["stacks"])
        if numStacks == 0:
            return []
        if len(chromeHangsObj["memoryMap"]) == 0:
            return []
        if len(chromeHangsObj["memoryMap"][0]) == 2:
            version = 3
        else:
            assert len(chromeHangsObj["memoryMap"][0]) == 4
            version = 2
        requestObj = {"stacks"    : chromeHangsObj["stacks"],
                      "memoryMap" : chromeHangsObj["memoryMap"],
                      "version"   : version}
    try:
        requestJson = json.dumps(requestObj)
        headers = { "Content-Type": "application/json" }
        requestHandle = urllib2.Request(SYMBOL_SERVER_URL, requestJson, headers)
        response = urllib2.urlopen(requestHandle, timeout=20)
    except Exception as e:
        sys.stderr.write("Exception while forwarding request: " + str(e) + "\n")
        sys.stderr.write(requestJson)
        return []
    try:
        responseJson = response.read()
    except Exception as e:
        sys.stderr.write("Exception while reading server response to symbolication request: " + str(e) + "\n")
        return []

    try:
        responseSymbols = json.loads(responseJson)
        # Sanity check
        if numStacks != len(responseSymbols):
            sys.stderr.write(str(len(responseSymbols)) + " hangs in response, " + str(numStacks) + " hangs in request!\n")
            return []

        # Sanity check
        for hangIndex in range(0, numStacks):
            if version == 1:
                stack = chromeHangsObj[hangIndex]["stack"]
            else:
                stack = chromeHangsObj["stacks"][hangIndex]
            requestStackLen = len(stack)
            responseStackLen = len(responseSymbols[hangIndex])
            if requestStackLen != responseStackLen:
                sys.stderr.write(str(responseStackLen) + " symbols in response, " + str(requestStackLen) + " PCs in request!\n")
                return []
    except Exception as e:
        sys.stderr.write("Exception while parsing server response to forwarded request: " + str(e) + "\n")
        return []

    return responseSymbols

def is_interesting(frame):
    if is_irrelevant(frame):
        return False
    if is_raw(frame):
        return False
    if is_js(frame):
        return False
    return True

def is_irrelevant(frame):
    m = irrelevantSignatureRegEx.match(frame)
    if m:
        return True

def is_raw(frame):
    m = rawAddressRegEx.match(frame)
    if m:
        return True

def is_js(frame):
    m = jsFrameRegEx.match(frame)
    if m:
        return True

def is_boring(frame):
    return frame in boringEventHandlingFrames

def is_interesting_lib(frame):
    for lib in interestingLibs:
        if lib in frame:
            return True
    return False

def min_json(obj):
    return json.dumps(obj, separators=(',', ':'))

def get_signature(stack):
    # From Vladan:
    # 1. Remove uninteresting frames from the top of the stack
    # 2. Keep removing raw addresses and JS frames from the top of the stack
    #    until you hit a real frame
    # 3. Get remaining top N frames (I used N = 15), or more if no
    #    xul.dll/firefox.exe/mozjs.dll in the top N frames (up to first
    #    xul.dll/etc frame plus one extra frame)
    # 4. From this subset of frames, remove all XRE_Main or generic event-
    #    handling frames from the bottom of stack (until you hit a real frame)
    # 5. Remove any raw addresses and JS frames from the bottom of stack (same
    #    as step 2 but done to the other end of the stack)

    interesting = [ is_interesting(f) for f in stack ]
    try:
        first_interesting_frame = interesting.index(True)
    except ValueError as e:
        # No interesting frames in stack.
        return []

    signature = stack[first_interesting_frame:]
    libby = [ is_interesting_lib(f) for f in signature ]
    try:
        last_interesting_frame = libby.index(True) + 1
    except ValueError as e:
        # No interesting library frames in stack, include them all
        last_interesting_frame = len(libby) - 1

    if last_interesting_frame < MIN_FRAMES:
        last_interesting_frame = MIN_FRAMES

    signature = signature[0:last_interesting_frame]
    boring = [ is_raw(f) or is_js(f) or is_boring(f) for f in signature ]
    # Pop raw addresses, JS frames, and boring stuff from the end
    while boring and boring.pop():
        signature.pop()

    return signature

def load_pings(fin):
    line_num = 0
    while True:
        uuid = fin.read(36)
        if len(uuid) == 0:
            break
        assert len(uuid) == 36
        line_num += 1
        tab = fin.read(1)
        assert tab == '\t'
        jsonstr = fin.readline()
        try:
            json_dict = json.loads(jsonstr)
        except Exception, e:
            print >> sys.stderr, "Error parsing json on line", line_num, ":", e
            continue
        yield line_num, uuid, json_dict

def handle_ping(args):
    line_num, uuid, json_dict = args
    hang_stacks = []
    reqs = 0
    errs = 0
    symbolicated = {}
    for kind in ["chromeHangs", "lateWrites"]:
        hangs = json_dict.get(kind)
        if hangs:
            del json_dict[kind]
            stacks = symbolicate(hangs)
            reqs += 1
            symbolicated[kind] = stacks
            if stacks == []:
                errs += 1

    if "histograms" in json_dict:
        del json_dict["histograms"]
    print "Handling line", line_num, uuid, "got", len(symbolicated["chromeHangs"]), "hangs,", len(symbolicated["lateWrites"]), "late writes."
    return line_num, uuid, json.dumps(json_dict), symbolicated["chromeHangs"], symbolicated["lateWrites"], reqs, errs

def get_stack_key(stack_entry, memoryMap):
    mm_idx, offset = stack_entry
    if mm_idx == -1:
        return None

    if mm_idx < len(memoryMap):
        mm = memoryMap[mm_idx]
        # cache on (dllname, debugid, offset)
        return (mm[0], mm[1], offset)

    return None

def combine_stacks(stacks):
    # Change from
    #   (lib, debugid, offset) => count
    # to
    #   (lib, debugid) => [ offsets ]
    combined = {}
    for lib, debugid, offset in stacks.keys():
        key = (lib, debugid)
        if key not in combined:
            combined[key] = []
        combined[key].append(offset)
    return combined

def process(input_file, output_file, submission_date, include_latewrites=False):
    # 1. First pass, extract (and count) all the unique stack elements
    if input_file == '-':
        fin = sys.stdin
    else:
        fin = open(input_file, "r")

    kinds = ["chromeHangs"]
    if include_latewrites:
        kinds.append("lateWrites")

    stack_cache = {}
    print "Extracting unique stack elements"
    for line_num, uuid, json_dict in load_pings(fin):
        cache_hits = 0
        cache_misses = 0
        for kind in kinds:
            hangs = json_dict.get(kind)
            if hangs:
                for stack in hangs["stacks"]:
                    for stack_entry in stack:
                        key = get_stack_key(stack_entry, hangs["memoryMap"])
                        if key is not None:
                            if key in stack_cache:
                                stack_cache[key] += 1
                            else:
                                stack_cache[key] = 1

    # 2. Filter out stack entries with fewer than MIN_HITS occurrences
    print "Filtering rare stack elements"
    if MIN_HITS > 1:
        to_be_symbolicated = { k: v for k, v in stack_cache.iteritems() if v > MIN_HITS }
    else:
        to_be_symbolicated = stack_cache

    # 3. Change from
    #      (lib, debugid, offset) => count
    #    to
    #      (lib, debugid) => [ offsets ]
    print "Combining stacks"
    combined_stacks = combine_stacks(to_be_symbolicated)

    # 4. For each library, try to fetch symbols for the given offsets.
    #      Use a local cache if available.
    # TODO
    # (lib, debugid, offset) => symbolicated string
    print "Looking up stack symbols"
    symbolicated_stacks = symbolicate(combined_stacks)

    # 5. Use these symbolicated stacks to symbolicate the actual data
    #    go back to the beginning the input file.
    print "Symbolicating data"
    fin.seek(0)
    fout = gzip.open(output_file, "wb")
    for line_num, uuid, json_dict in load_pings(fin):
        for kind in kinds:
            hangs = json_dict.get(kind)
            if hangs:
                hangs["stacksSymbolicated"] = []
                for stack in hangs["stacks"]:
                    sym = []
                    for stack_entry in stack:
                        key = get_stack_key(stack_entry, hangs["memoryMap"])
                        if key is None:
                            sym.append("-0x1")
                        elif key not in symbolicated_stacks:
                            #print "Missed key:", key
                            sym.append(hex(stack_entry[1]))
                        else:
                            sym.append(symbolicated_stacks[key])
                    hangs["stacksSymbolicated"].append(sym)
        fout.write(submission_date)
        fout.write("\t")
        fout.write(uuid)
        fout.write("\t")
        fout.write(min_json(json_dict))
        fout.write("\n")
    fin.close()
    fout.close()

    print "All done."

    # symbolication_errors = 0
    # symbolication_requests = 0
    # pool = Pool(processes=20)
    # result = pool.imap_unordered(handle_ping, load_pings(fin))
    # pool.close()
    # while True:
    #     try:
    #         line_num, uuid, payload, hang_stacks, late_writes_stacks, reqs, errs = result.next(1)
    #         symbolication_errors += errs
    #         symbolication_requests += reqs
    #         fout.write(submission_date)
    #         fout.write("\t")
    #         fout.write(uuid)
    #         fout.write("\t")
    #         fout.write(payload)

    #         for stack in hang_stacks:
    #             #fout.write("\n----- BEGIN HANG SIGNATURE -----\n")
    #             #fout.write("\n".join(get_signature(stack)))
    #             #fout.write("\n----- END HANG SIGNATURE -----\n")
    #             fout.write("\n----- BEGIN HANG STACK -----\n")
    #             fout.write("\n".join(stack))
    #             fout.write("\n----- END HANG STACK -----\n")

    #         for stack in late_writes_stacks:
    #             fout.write("\n----- BEGIN LATE WRITE STACK -----\n")
    #             fout.write("\n".join(stack))
    #             fout.write("\n----- END LATE WRITE STACK -----\n")
    #     except multiprocessing.TimeoutError:
    #         print "no results yet.."
    #     except StopIteration:
    #         break
    # pool.join()
    # sys.stderr.write("Requested %s symbolications. Got %s errors." % (symbolication_requests, symbolication_errors))

class Usage(Exception):
    def __init__(self, msg):
        self.msg = msg


def main(argv=None):
    if argv is None:
        argv = sys.argv
    try:
        try:
            opts, args = getopt.getopt(argv[1:], "hi:o:d:v", ["help", "input=", "output=", "date="])
        except getopt.error, msg:
            raise Usage(msg)

        input_file = None
        output_file = None
        submission_date = None
        # option processing
        for option, value in opts:
            if option == "-v":
                verbose = True
            if option in ("-h", "--help"):
                raise Usage(help_message)
            if option in ("-i", "--input"):
                input_file = value
            if option in ("-o", "--output"):
                output_file = value
            if option in ("-d", "--date"):
                submission_date = value
        process(input_file, output_file, submission_date)
    except Usage, err:
        print >> sys.stderr, sys.argv[0].split("/")[-1] + ": " + str(err.msg)
        print >> sys.stderr, " for help use --help"
        return 2

if __name__ == "__main__":
    sys.exit(main())
