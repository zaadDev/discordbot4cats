r"""
Un bot che riproduce suoni dal fs

/air_horn   Joina il canale dell'utente che fa il comando e riproduce un airhorn.webm ed esce.
            Viene eseguito solo se il bot è in modalità passiva.
/join   Entra nel canale vocale e riproduce la playlist.
/pause  Pausa la riproduzione, dopo 3 minuti esce dal canale vocale.
/play   Riprende la riproduzione se in un canale vocale.
/stop   Stoppa la riproduzione ed esce dal canale vocale.
/skip   Solo se non è nel suo canale vocale permette di skippare la canzone corrente.
/sync   Bot owner/developers Only. Sincronizza i comandi del bot dopo un aggiornamento.

Il bot è in modalità passiva quando non c'è nessuno ad ascoltare canzoni nel suo canale vocale.
In modalità passiva riproduce delle canzoni 24/7 nel suo canale vocale.


La playlist viene costruita randomicamente dalla cartella /songs/ nella cartella degli assets
quando il bot viene inizializzato e ogni volta che finisce di riprodurre la playlist.
Questa è la struttura che ci si aspetta:

/assets
|
\---songs
    +---album
    |       song.webm
    |       song3.webm
    |       song4.webm
    |
    +---album1
    |       song2.webm
    |       song7.webm
    |
    \---album2
            song1.webm
            song8.webm

Extra:
Ha uno stato "Playing..." con la canzone corrente
In modalità passiva per non consumare banda se non c'è nessuno nel canale vocale non manda pacchetti audio.
"""

import argparse
import asyncio
import json
import logging
import logging.handlers
import os
import random
import sys
from pathlib import Path
from typing import Self, TypedDict

import discord
from discord import app_commands
from discord.ext import commands

# Setup a new logger for CatFM
catfmlog = logging.getLogger("discord.catfm")
catfmlog.setLevel(logging.DEBUG)
discord.utils.setup_logging(level=logging.DEBUG)


class FileConfDict(TypedDict):
    """TypedDict for default conf file. That's useful for type Hinting."""

    token: str
    guilds: list[int]
    fm_channel: int
    assets: str


class CatFMConf(FileConfDict):
    """TypedDict for storing bot configuration settings derived from FileConfDict."""

    sync: bool


# Globals&Defaults
CONFIG_FILE: str = "catFm.conf.json"
ASSETS_FOLD: str = "./assets/"
DEFAULT_CONF: FileConfDict = {
    "token": "",
    "guilds": list(),
    "fm_channel": int(),
    "assets": "./assets/",
}
DEFAULT_BOT_CONF: CatFMConf = {**DEFAULT_CONF, "sync": False}


class CatFM(commands.bot.Bot):
    def __init__(self: Self, conf: CatFMConf, *args, **kwargs) -> None:
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=discord.Intents.default(),
            *args,
            **kwargs,
        )
        if conf is None:
            raise TypeError("Conf can't be None")
        self.init = False
        self.guild_sessions = dict()
        self.conf = conf
        self.do_sync = conf["sync"]
        self.songs_sfolders_path = Path(self.conf["assets"] + "songs/")
        self.playable_songs = None

    async def setup_hook(self: Self) -> None:
        await self.add_cog(CatFMCogs(self))
        self.playable_songs = self.get_songs()
        if self.do_sync:
            catfmlog.debug("Syncronizzazione comandi")
            await self.tree.sync()
            self.do_sync = False
        return

    # TODO: Handle failures
    # TODO: Formalize sessions
    # TODO: Maybe should be called in this event https://discordpy.readthedocs.io/en/stable/api.html#discord.on_guild_available
    async def init_guild_sessions(self, sguild=None):
        """
        if sguild is None: Inizializza tutte le sessioni per le gilde dove il bot è membro presenti in self.conf
        else: inizializza la sessione per la singola gilda se il bot è membro
        """
        if sguild is None:
            for guild_id in self.conf["guilds"]:
                guild = self.get_guild(guild_id)
                if guild in self.guilds:
                    self.guild_sessions[guild] = dict()
        else:
            guild = self.get_guild(sguild)
            if guild in self.guilds:
                self.guild_sessions[guild] = dict()

    async def on_ready(self: Self):
        # Initialize only once
        if not self.init:
            # Create sessions for guilds in config if bot is in such guilds
            await self.init_guild_sessions()
            self.init = True
            catfmlog.info(
                f"Sessioni inizializzate per i server: {[i.name for i in self.guilds]}"
            )
        catfmlog.info("Catfm connesso e pronto")

    def get_songs(self) -> dict[str, tuple[str, Path]]:
        """Returns all the songs from the folder
        {song_name: (song_album, song_path)}
        """
        errors = list()
        songs: dict[str, tuple[str, Path]] = dict()
        for albumPath in self.songs_sfolders_path.iterdir():
            if albumPath.is_dir() and os.access(albumPath, os.R_OK):
                for songPath in albumPath.iterdir():
                    if songPath.exists() and os.access(songPath, os.R_OK):
                        clean_song = songPath.stem.replace("_", " ")
                        clean_album = albumPath.stem.replace("_", " ")
                        songs[clean_song] = (clean_album, songPath)
                    else:
                        errors.append(
                            (
                                songPath,
                                f"exist:{songPath.exists()},read:{os.access(songPath, os.R_OK)}",
                            )
                        )
            else:
                errors.append((albumPath))
        if errors:
            catfmlog.info("Errors occurred with these paths", errors)
            catfmlog.info(f"Songs loaded: {songs}")
        return songs

    # TODO: Probably need optimizations... look for iterators
    def get_playlist(self: Self):
        if not self.playable_songs:
            self.playable_songs = self.get_songs()
        return random.sample(
            tuple(self.playable_songs.keys()), k=len(self.playable_songs.keys())
        )

    def get_playlist_iter(self: Self, playlist: list):
        while playlist:
            yield playlist.pop()


class CatFMCogs(commands.Cog):
    class BusyCheckFailure(app_commands.AppCommandError):
        """Exception for ensure_bot_not_busy"""

        # TODO: Specificare cos'è qualcos'altro
        def __init__(self):
            message = "Il Bot era occupato a fare qualcos' altro"
            super().__init__(message)

    def __init__(self: Self, bot: CatFM) -> None:
        super().__init__()
        self.bot = bot
        self.playing = False

    async def cog_app_command_error(
        self: Self,
        interaction: discord.Interaction[discord.Client],
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, CatFMCogs.BusyCheckFailure):
            catfmlog.debug("BusyCheckFailure happened")
            catfmlog.debug(error, exc_info=True)
            await interaction.response.send_message(
                "Il bot è occupato a fare qualcos'altro", ephemeral=True
            )
        return await super().cog_app_command_error(interaction, error)

    @commands.is_owner()
    @app_commands.command()
    async def sync(self: Self, interaction: discord.Interaction):
        await self.bot.remove_cog("CatFMCogs")
        await self.bot.add_cog(CatFMCogs(self.bot))
        await self.bot.tree.sync()
        await interaction.response.send_message(
            "Commands should be Synced", ephemeral=True
        )

    # TODO: Traccia cosa sta attualmente facendo il bot per gilda
    # TODO: Attualmente è busy se sta in un qualsiasi canale vocale, che non è la cosa richiesta
    @staticmethod
    async def ensure_bot_not_busy(interaction: discord.Interaction) -> bool:
        if interaction.guild and isinstance(
            interaction.guild.voice_client, discord.VoiceProtocol
        ):
            raise CatFMCogs.BusyCheckFailure()
        return (
            not isinstance(interaction.guild.voice_client, discord.VoiceProtocol)
            if interaction.guild
            else False
        )

    # TODO: this need to be refactored for cleanups and reduce duplicate code
    @app_commands.check(ensure_bot_not_busy)
    @app_commands.guild_only()
    @app_commands.command()
    async def join(self: Self, interaction: discord.Interaction):
        """Entra in un canale vocale e riproduce la playlist"""
        if (
            isinstance(interaction.user, discord.Member)
            and interaction.user.voice
            and interaction.user.voice.channel
        ):
            session = self.bot.guild_sessions[interaction.guild]
            if "playlist" not in session.keys() or not session["playlist"]:
                session["playlist"] = self.bot.get_playlist()
            voice = await interaction.user.voice.channel.connect()

            await interaction.response.send_message(
                "Entro nel canale vocale", ephemeral=True
            )

            def wrap_play_next_song(error):
                # That's why: https://discordpy.readthedocs.io/en/stable/faq.html#how-do-i-pass-a-coroutine-to-the-player-s-after-function
                if error:
                    print(f"Errore player {error}")
                try:
                    song_name = next(self.bot.get_playlist_iter(session["playlist"]))
                except StopIteration:
                    session["playlist"] = self.bot.get_playlist()
                    if session["playlist"]:
                        song_name = next(
                            self.bot.get_playlist_iter(session["playlist"])
                        )
                    else:
                        catfmlog.debug(
                            "Ho provato a riempire la lista ma è rimasta vuota"
                        )
                        return
                song_path = self.bot.playable_songs[song_name][1]
                presence = discord.Game(song_name)
                asyncio.run_coroutine_threadsafe(
                    self.bot.change_presence(
                        status=discord.Status.idle, activity=presence
                    ),
                    self.bot.loop,
                )
                future = asyncio.run_coroutine_threadsafe(
                    discord.FFmpegOpusAudio.from_probe(song_path), self.bot.loop
                )
                try:
                    source = future.result()
                except Exception as exc:
                    catfmlog.debug(f"Errore probing file: {song_path}\n\t\t`> {exc}")
                else:
                    # No Errors Raised... I guess
                    try:
                        voice.play(source, after=wrap_play_next_song)
                    except Exception as e:
                        catfmlog.debug(f"Si è verificata un eccezione {e}")

            song_name = next(self.bot.get_playlist_iter(session["playlist"]))
            song_path = self.bot.playable_songs[song_name][1]
            presence = discord.Game(song_name)
            await self.bot.change_presence(
                status=discord.Status.idle, activity=presence
            )
            source = await discord.FFmpegOpusAudio.from_probe(str(song_path))
            try:
                voice.play(source, after=wrap_play_next_song)
            except Exception as e:
                catfmlog.debug(f"Si è verificata un eccezione {e}")
        else:
            await interaction.response.send_message(
                "Non sei in un canale vocale al momento"
            )

    @app_commands.check(ensure_bot_not_busy)
    @app_commands.guild_only()
    @app_commands.command()
    async def air_horn(self: Self, interaction: discord.Interaction):
        if (
            isinstance(interaction.user, discord.Member)
            and interaction.user.voice
            and interaction.user.voice.channel
        ):
            voice = await interaction.user.voice.channel.connect()
            source = await discord.FFmpegOpusAudio.from_probe(
                self.bot.conf["assets"] + r"airhorn.webm"
            )
            await interaction.response.send_message(
                "Entro nel canale vocale", ephemeral=True
            )

            def wrap_disconnect(error):
                if error:
                    print(f"Errore player {error}")
                if voice.is_connected():
                    coro = voice.disconnect()
                fut = asyncio.run_coroutine_threadsafe(coro, self.bot.loop)
                try:
                    fut.result()
                except Exception as exc:
                    print(f"Errore nel disconnettersi {exc}")

            voice.play(source, after=wrap_disconnect)
        else:
            await interaction.response.send_message(
                "Non sei in un canale vocale al momento", ephemeral=True
            )


def configurate(args: argparse.Namespace) -> CatFMConf:
    configfp: str = args.config if args.config else CONFIG_FILE
    assetsfp: str = args.assets if args.assets else ASSETS_FOLD
    global DEFAULT_CONF

    # Crea nuovo file di configurazione se non esiste a partire da DEFULTCONF
    if not os.path.exists(configfp):
        with open(configfp, mode="x", encoding="utf8") as f:
            f.write(json.dumps(DEFAULT_CONF))

    if not os.path.isfile(configfp):
        raise FileNotFoundError(f"{configfp} file not Found or Not a file")
    if not os.access(configfp, os.R_OK):
        raise PermissionError(f"{configfp} file not Readable.")
    with open(configfp, mode="r", encoding="utf8") as f:
        try:
            conf = json.load(f)
        except json.decoder.JSONDecodeError as error:
            raise json.decoder.JSONDecodeError(
                msg=f"Il file di configurazione {configfp} ha errori di sintassi (json)",
                doc=error.doc,
                pos=error.pos,
            ) from error

    if conf:
        # TODO: Should checks assets too add if missing trailing '/'
        conf["assets"] = assetsfp
        conf["sync"] = args.sync if args.sync else DEFAULT_BOT_CONF["sync"]
    else:
        raise TypeError(f"Config {configfp}")
    # TODO: Validate conf...
    return conf


def parser_setup(argv: list[str]):
    cli_parser = argparse.ArgumentParser(
        prog="catfm.py", description="Discord Bot for Cats"
    )
    cli_parser.add_argument(
        "--config",
        default=CONFIG_FILE,
        action="store",
        metavar="<./conf.json>",
        help=f"json file containing the configurations for catfm. Defaults to {CONFIG_FILE}",
    )
    cli_parser.add_argument(
        "--assets",
        default=ASSETS_FOLD,
        action="store",
        metavar="<./assets/>",
        help=f"folder containing opus encoded sounds, supports subfolders. Overrides the config.json. Defaults to {ASSETS_FOLD}",
    )
    cli_parser.add_argument(
        "--sync",
        default=False,
        action="store_true",
        help="Syncs bots commands on startup. This is meant to be used only once abusing it may result in Discord Rate Limiting your bot",
    )
    args = cli_parser.parse_args(argv)
    return args


if __name__ == "__main__":
    args = parser_setup(sys.argv[1:])
    catfmlog.debug(f"Cli args {args}")
    conf = configurate(args)
    client = CatFM(conf)
    client.run(conf["token"], log_handler=None)
