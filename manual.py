#!/usr/bin/env python

import sys
import os
import locale
import glob
import argparse
import struct
import logging
import re
from extensions import valid_tagging_extensions
from readSettings import ReadSettings
from tvdb_mp4 import Tvdb_mp4
from tmdb_mp4 import tmdb_mp4
from mkvtomp4 import MkvtoMp4
from post_processor import PostProcessor
from tvdb_api import tvdb_api
from tmdb_api import tmdb
from extensions import tmdb_api_key
from logging.config import fileConfig
from guessit import guessit
from mutagen.mp4 import MP4

if sys.version[0] == "3":
    raw_input = input

logpath = '/var/log/sickbeard_mp4_automator'
if os.name == 'nt':
    logpath = os.path.dirname(sys.argv[0])
elif not os.path.isdir(logpath):
    try:
        os.mkdir(logpath)
    except:
        logpath = os.path.dirname(sys.argv[0])
configPath = os.path.abspath(os.path.join(os.path.dirname(sys.argv[0]), 'logging.ini')).replace("\\", "\\\\")
logPath = os.path.abspath(os.path.join(logpath, 'index.log')).replace("\\", "\\\\")
fileConfig(configPath, defaults={'logfilename': logPath})

log = logging.getLogger("MANUAL")
logging.getLogger("subliminal").setLevel(logging.CRITICAL)
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("enzyme").setLevel(logging.WARNING)
logging.getLogger("qtfaststart").setLevel(logging.WARNING)

log.info("Manual processor started.")

settings = ReadSettings(os.path.dirname(sys.argv[0]), "autoProcess.ini", logger=log)

def mediatype():
    print("Select media type:")
    print("1. Movie (via IMDB ID)")
    print("2. Movie (via TMDB ID)")
    print("3. TV")
    print("4. Convert without tagging")
    print("5. Skip file")
    result = raw_input("#: ")
    if 0 < int(result) < 6:
        return int(result)
    else:
        print("Invalid selection")
        return mediatype()


def getValue(prompt, num=False):
    print(prompt + ":")
    value = raw_input("#: ").strip(' \"')
    # Remove escape characters in non-windows environments
    if os.name != 'nt':
        value = value.replace('\\', '')
    try:
        value = value.decode(sys.stdout.encoding)
    except:
        pass
    if num is True and value.isdigit() is False:
        print("Must be a numerical value")
        return getValue(prompt, num)
    else:
        return value


def getYesNo():
    yes = ['y', 'yes', 'true', '1']
    no = ['n', 'no', 'false', '0']
    data = raw_input("# [y/n]: ")
    if data.lower() in yes:
        return True
    elif data.lower() in no:
        return False
    else:
        print("Invalid selection")
        return getYesNo()


def getinfo(fileName=None, silent=False, tag=True, tvdbid=None, imdbid=None):
    tagdata = None
    # Try to guess the file is guessing is enabled
    if fileName is not None:
        tagdata = guessInfo(fileName, tvdbid, imdbid)

    if silent is False:
        if tagdata:
            print("Proceed using guessed identification from filename?")
            if getYesNo():
                return tagdata
        else:
            print("Unable to determine identity based on filename, must enter manually")
        m_type = mediatype()
        if m_type is 3:
            tvdbid = getValue("Enter TVDB Series ID", True)
            season = getValue("Enter Season Number", True)
            episode = getValue("Enter Episode Number", True)
            return m_type, tvdbid, season, episode
        elif m_type is 1:
            imdbid = getValue("Enter IMDB ID")
            return m_type, imdbid
        elif m_type is 2:
            tmdbid = getValue("Enter TMDB ID", True)
            return m_type, tmdbid
        elif m_type is 4:
            return None
        elif m_type is 5:
            return False
    else:
        if tagdata and tag:
            return tagdata
        else:
            return None


def guessInfo(fileName, tvdbid=None, imdbid=None):
    if not settings.fullpathguess:
        fileName = os.path.basename(fileName)

    guess = guessit(fileName)
    yearGuess = str(guess['year']) if 'year' in guess else ''
    print("Guessing title of '%s%s' for file '%s'" % (guess["title"], ' (' + yearGuess + ')' if yearGuess else '', fileName))
    try:
        if guess['type'] == 'movie' and tvdbid is None:
            return tmdbInfo(guess)
        elif guess['type'] == 'movie' and tvdbid is not None:
            raise Exception("Guess returned movie type even though tvdb ID was given")
        elif guess['type'] == 'episode':
            return tvdbInfo(guess, tvdbid)
        else:
            return None
    except Exception as e:
        print("Guessing title failed: %s" % (str(e.args[0])))
        return None


def tmdbInfo(guessData):
    origname = ''.join(e for e in guessData["title"] if e.isalnum())
    # origname = origname.replace('&', 'and')
    origReleaseYear = guessData["year"] if "year" in guessData else 0

    tmdb.configure(tmdb_api_key)
    movies = tmdb.Movies(guessData["title"].encode('ascii', errors='ignore'), limit=4)
    for movie in movies.iter_results():
        # Identify the first movie in the collection that matches exactly the movie title
        foundname = ''.join(e for e in movie["title"] if e.isalnum())
        foundReleaseYear = ''.join(e for e in movie["release_date"][:4] if e.isalnum())
        if foundname.lower() == origname.lower() and (origReleaseYear == 0 or str(foundReleaseYear) == str(origReleaseYear)):
            print("Matched movie as %s released %s" % (movie["title"].encode(sys.stdout.encoding or 'utf-8', errors='ignore'), movie["release_date"].encode(sys.stdout.encoding or 'utf-8', errors='ignore')))
            movie = tmdb.Movie(movie["id"])
            if isinstance(movie, dict):
                tmdbid = movie["id"]
            else:
                tmdbid = movie.get_id()
            return 2, tmdbid
        else:
            print("Skipped potential match: %s released %s" % (movie["title"].encode(sys.stdout.encoding or 'utf-8', errors='ignore'), movie["release_date"].encode(sys.stdout.encoding or 'utf-8', errors='ignore')))
    print("Did not find movie with the search '%s' released '%s'" % (origname.lower(), str(origReleaseYear)))
    return None


def tvdbInfo(guessData, tvdbid=None):
    series = guessData["title"]
    if 'year' in guessData:
        fullseries = series + " (" + str(guessData["year"]) + ")"
    season = guessData["season"]
    episode = guessData["episode"]
    t = tvdb_api.Tvdb(interactive=False, cache=False, banners=False, actors=False, forceConnect=True, language='en')
    try:
        tvdbid = str(tvdbid) if tvdbid else t[fullseries]['id']
        series = t[int(tvdbid)]['seriesname']
    except:
        tvdbid = t[series]['id']
    try:
        print("Matched TV episode as %s (TVDB ID:%d) S%02dE%02d" % (series.encode(sys.stdout.encoding or 'utf-8', errors='ignore'), int(tvdbid), int(season), int(episode)))
    except:
        print("Matched TV episode")
    return 3, tvdbid, season, episode


def processFile(inputfile, tagdata, relativePath=None):
    # Gather tagdata
    if tagdata is False:
        return  # This means the user has elected to skip the file
    elif tagdata is None:
        tagmp4 = None  # No tag data specified but convert the file anyway
    elif tagdata[0] is 1:
        imdbid = tagdata[1]
        tagmp4 = tmdb_mp4(imdbid, language=settings.taglanguage, logger=log)
        try:
            print("Processing %s" % (tagmp4.title.encode(sys.stdout.encoding or 'utf-8', errors='ignore')))
        except:
            print("Processing movie")
    elif tagdata[0] is 2:
        tmdbid = tagdata[1]
        tagmp4 = tmdb_mp4(tmdbid, True, language=settings.taglanguage, logger=log)
        try:
            print("Processing %s" % (tagmp4.title.encode(sys.stdout.encoding or 'utf-8', errors='ignore')))
        except:
            print("Processing movie")
    elif tagdata[0] is 3:
        tvdbid = int(tagdata[1])
        season = int(tagdata[2])
        # some episodes are combined and return `[1, 2]`` instead of just `1`
        episode = int(tagdata[3][0]) if type(tagdata[3]) == list else int(tagdata[3])
        tagmp4 = Tvdb_mp4(tvdbid, season, episode, language=settings.taglanguage, logger=log)
        try:
            print("Processing %s Season %02d Episode %02d - %s" % (tagmp4.show.encode(sys.stdout.encoding or 'utf-8', errors='ignore'), int(tagmp4.season), int(tagmp4.episode), tagmp4.title.encode(sys.stdout.encoding, errors='ignore')))
        except:
            print("Processing TV episode")

    # Process
    if MkvtoMp4(settings, logger=log).validSource(inputfile):
        converter = MkvtoMp4(settings, logger=log)
        output = converter.process(inputfile, True)
        if output:
            if tagmp4 is not None and output['output_extension'] in valid_tagging_extensions:
                taggingFailed = False
                try:
                    tagmp4.setHD(output['x'], output['y'])
                    tagmp4.writeTags(output['output'], settings.artwork, settings.thumbnail)
                except Exception as e:
                    taggingFailed = True
                    print("There was an error tagging the file:")
                    print(e)
                
                if taggingFailed:
                    print("Manually tagging file based on file name")
                    
                    # tag media based on file name
                    try:
                        isTvShow = tagdata[0] is 3
                        if isTvShow:
                            writeTagsTvManual(output, inputfile)
                        else:
                            writeTagsMovieManual(output, inputfile)
                    except Exception as e:
                        print("There was a problem writing the manual tags:")
                        print(e)
                    
            print("Passed tagging", tagmp4 is not None, output['output_extension'])
            
            if settings.relocate_moov and output['output_extension'] in valid_tagging_extensions:
                converter.QTFS(output['output'])
            output_files = converter.replicate(output['output'], relativePath=relativePath)
            if settings.postprocess:
                post_processor = PostProcessor(output_files)
                if tagdata:
                    if tagdata[0] is 1:
                        post_processor.setMovie(tagdata[1])
                    elif tagdata[0] is 2:
                        post_processor.setMovie(tagdata[1])
                    elif tagdata[0] is 3:
                        post_processor.setTV(tagdata[1], tagdata[2], tagdata[3])
                post_processor.run_scripts()

def writeTagsTvManual(output, path):
    fileName = os.path.splitext(os.path.basename(path))[0]

    nameParts = fileName.split(' - ')
    show = nameParts[0]
    season = int(re.search('S[0-9]+E', nameParts[1]).group(0)[1:-1])
    episode = int(re.search('E[0-9]+', nameParts[1]).group(0)[1:])
    title = ' - '.join(nameParts[2:])

    if show is None or season is None or episode is None or title is None:
        raise IOError("File name not in expected format: 'show title - S##E## - episode title'")

    video = MP4(path)

    video["tvsh"] = show  # TV show title
    video["\xa9nam"] = title  # Video title
    video["tven"] = title  # Episode title
    video["tvsn"] = [season]  # Season number
    video["disk"] = [(int(season), 0)]  # Season number as disk
    video["\xa9alb"] = show + ", Season " + str(season) # iTunes Album as Season
    video["tves"] = [episode]  # Episode number
    video["stik"] = [10]  # TV show iTunes category

    MP4(path).delete(path)
    for i in range(3):
        try:
            print("Trying to write manual tags.")
            video.save()
            print("Manual tags written successfully.")
            break
        except IOError as e:
            print("There was a problem writing the manual tags. Retrying.")
            time.sleep(5)

def writeTagsMovieManual(output, path):
    fileName = os.path.splitext(os.path.basename(path))[0]

    nameParts = fileName.split(' (')
    year = str.replace(nameParts[-1], ')', '')
    title = ' ('.join(nameParts[:-1])

    if year is None or title is None:
        raise IOError("File name not in expected format: 'movie name (year)'")

    video = MP4(path)

    video["\xa9nam"] = title  # Video title
    video["\xa9day"] = year

    MP4(path).delete(path)
    for i in range(3):
        try:
            print("Trying to write manual tags.")
            video.save()
            print("Manual tags written successfully.")
            break
        except IOError as e:
            print("There was a problem writing the manual tags. Retrying.")
            print(e)
            time.sleep(5)

def walkDir(dir, silent=False, preserveRelative=False, tvdbid=None, tag=True, imdbid=None):
    for r, d, f in os.walk(dir):
        for file in f:
            filepath = os.path.join(r, file)
            relative = os.path.split(os.path.relpath(filepath, dir))[0] if preserveRelative else None
            try:
                if MkvtoMp4(settings, logger=log).validSource(filepath):
                    try:
                        print("Processing file %s" % (filepath.encode(sys.stdout.encoding or 'utf-8', errors='ignore')))
                    except:
                        try:
                            print("Processing file %s" % (filepath.encode('utf-8', errors='ignore')))
                        except:
                            print("Processing file")
                    if tag:
                        tagdata = getinfo(filepath, silent, tvdbid=tvdbid, imdbid=imdbid)
                    else:
                        tagdata = None
                    processFile(filepath, tagdata, relativePath=relative)
            except Exception as e:
                print("An unexpected error occurred, processing of this file has failed")
                print(str(e))


def main():
    global settings

    parser = argparse.ArgumentParser(description="Manual conversion and tagging script for sickbeard_mp4_automator")
    parser.add_argument('-i', '--input', help='The source that will be converted. May be a file or a directory')
    parser.add_argument('-c', '--config', help='Specify an alternate configuration file location')
    parser.add_argument('-a', '--auto', action="store_true", help="Enable auto mode, the script will not prompt you for any further input, good for batch files. It will guess the metadata using guessit")
    parser.add_argument('-tv', '--tvdbid', help="Set the TVDB ID for a tv show")
    parser.add_argument('-s', '--season', help="Specifiy the season number")
    parser.add_argument('-e', '--episode', help="Specify the episode number")
    parser.add_argument('-imdb', '--imdbid', help="Specify the IMDB ID for a movie")
    parser.add_argument('-tmdb', '--tmdbid', help="Specify theMovieDB ID for a movie")
    parser.add_argument('-nm', '--nomove', action='store_true', help="Overrides and disables the custom moving of file options that come from output_dir and move-to")
    parser.add_argument('-nc', '--nocopy', action='store_true', help="Overrides and disables the custom copying of file options that come from output_dir and move-to")
    parser.add_argument('-nd', '--nodelete', action='store_true', help="Overrides and disables deleting of original files")
    parser.add_argument('-nt', '--notag', action="store_true", help="Overrides and disables tagging when using the automated option")
    parser.add_argument('-np', '--nopost', action="store_true", help="Overrides and disables the execution of additional post processing scripts")
    parser.add_argument('-pr', '--preserveRelative', action='store_true', help="Preserves relative directories when processing multiple files using the copy-to or move-to functionality")
    parser.add_argument('-cmp4', '--convertmp4', action='store_true', help="Overrides convert-mp4 setting in autoProcess.ini enabling the reprocessing of mp4 files")
    parser.add_argument('-m', '--moveto', help="Override move-to value setting in autoProcess.ini changing the final destination of the file")

    args = vars(parser.parse_args())

    # Setup the silent mode
    silent = args['auto']
    tag = True

    print("%sbit Python." % (struct.calcsize("P") * 8))

    # Settings overrides
    if(args['config']):
        if os.path.exists(args['config']):
            print('Using configuration file "%s"' % (args['config']))
            settings = ReadSettings(os.path.split(args['config'])[0], os.path.split(args['config'])[1], logger=log)
        elif os.path.exists(os.path.join(os.path.dirname(sys.argv[0]), args['config'])):
            print('Using configuration file "%s"' % (args['config']))
            settings = ReadSettings(os.path.dirname(sys.argv[0]), args['config'], logger=log)
        else:
            print('Configuration file "%s" not present, using default autoProcess.ini' % (args['config']))
    if (args['nomove']):
        settings.output_dir = None
        settings.moveto = None
        print("No-move enabled")
    elif (args['moveto']):
        settings.moveto = args['moveto']
        print("Overriden move-to to " + args['moveto'])
    if (args['nocopy']):
        settings.copyto = None
        print("No-copy enabled")
    if (args['nodelete']):
        settings.delete = False
        print("No-delete enabled")
    if (args['convertmp4']):
        settings.processMP4 = True
        print("Reprocessing of MP4 files enabled")
    if (args['notag']):
        settings.tagfile = False
        print("No-tagging enabled")
    if (args['nopost']):
        settings.postprocess = False
        print("No post processing enabled")

    # Establish the path we will be working with
    if (args['input']):
        path = (str(args['input']))
        try:
            path = glob.glob(path)[0]
        except:
            pass
    else:
        path = getValue("Enter path to file")

    tvdbid = int(args['tvdbid']) if args['tvdbid'] else None
    imdbid = int(args['imdbid']) if args['imdbid'] else None
    if os.path.isdir(path):
        walkDir(path, silent, tvdbid=tvdbid, preserveRelative=args['preserveRelative'], tag=settings.tagfile, imdbid=imdbid)
    elif (os.path.isfile(path) and MkvtoMp4(settings, logger=log).validSource(path)):
        if (not settings.tagfile):
            tagdata = None
        elif (args['tvdbid'] and not (args['imdbid'] or args['tmdbid'])):
            season = int(args['season']) if args['season'] else None
            episode = int(args['episode']) if args['episode'] else None
            if (tvdbid and season and episode):
                tagdata = [3, tvdbid, season, episode]
            else:
                tagdata = getinfo(path, silent=silent, tvdbid=tvdbid, imdbid=imdbid)
        elif ((args['imdbid'] or args['tmdbid']) and not args['tvdbid']):
            if (args['imdbid']):
                imdbid = args['imdbid']
                tagdata = [1, imdbid]
            elif (args['tmdbid']):
                tmdbid = int(args['tmdbid'])
                tagdata = [2, tmdbid]
        else:
            tagdata = getinfo(path, silent=silent, tvdbid=tvdbid, imdbid=imdbid)
        processFile(path, tagdata)
    else:
        try:
            print("File %s is not in the correct format" % (path))
        except:
            print("File is not in the correct format")


if __name__ == '__main__':
    main()
