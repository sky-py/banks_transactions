import time
from math import ceil
from contextlib import redirect_stdout
from dataclasses import dataclass
from io import TextIOBase
from threading import RLock
from loguru import logger
from rich.align import Align
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.padding import Padding
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn
from rich.table import Table
from rich.text import Text


def some_api_work(iteration):
    print(f'API start task {iteration}')


class RichLog:
    def __init__(self, header, header_style='bold white on blue') -> None:
        self.header_style = header_style
        self.request_style = 'bold bright_green'
        self.original_console_style = 'italic white'
        self.log_style = 'italic white'
        self._request = ''
        self._original_console = ''
        self._log = []
        self.console_to_rich_log_redirector = ConsoleToRich(self)
        self._console = Console()
        self._layout = Layout()
        self._layout.split_column(
            Layout(name='header', size=3),
            Layout(name='request_row', size=3),
            Layout(name='progress', size=1),
            Layout(name='log', ratio=1),
        )
        self._layout['request_row'].split_row(Layout(name='request', ratio=5), Layout(name='original_console', ratio=2))

        self._layout['progress'].visible = False
        self._layout['request_row'].visible = False
        self._layout['request'].visible = False
        self._layout['original_console'].visible = False

        self._progress = Progress(
            TextColumn('[bold blue]До запроса: {task.fields[remaining]}с'),
            BarColumn(bar_width=None, complete_style='bright_green'),
            TextColumn('[progress.percentage]{task.percentage:>3.0f}%'),
            console=self._console,
            expand=True,
        )

        self._live = Live(self._layout, console=self._console, screen=True)
        self._layout['header'].update(
            Panel(Align(header, style=self.header_style, align='center'), height=3, expand=True)
        )
        # self._layout['original_console'].update(Padding(self._original_console, (0, 2)))
        self._layout['progress'].update(Padding(self._progress, (0, 2)))
        self._live.start()

    @property
    def visible_log(self) -> Text:
        console_width = self._console.size.width - 4  #  ширина терминала − 2(border) − 2(padding по горизонтали)
        log_height = self._layout['log'].size or (
            self._console.size.height - 6
        )  # — подсчитываем, сколько строк помещается в области логов
        max_lines = max(0, log_height - 2)  # вычитаем 2 строки для рамки
        wrapped_lines = []
        for msg in self._log:
            text_obj = Text.from_ansi(msg)
            # text_obj = Text(msg, style=self.log_style)
            lines = text_obj.wrap(self._console, console_width)
            wrapped_lines.extend(lines)
        visible_lines = wrapped_lines[-max_lines:]

        log_visible = Text()
        for line in visible_lines:
            log_visible.append(line)
            log_visible.append('\n')
        return log_visible

    def _update_screen(self) -> None:
        self._layout['request'].update(
            Panel(Text(self._request, style=self.request_style), title='Status', height=3, expand=True)
        )
        self._layout['original_console'].update(
            Panel(Text(self._original_console, style=self.original_console_style), title='API', height=3, expand=True)
        )
        self._layout['log'].update(Panel(self.visible_log, title='Log', expand=True, padding=(0, 1)))
        self._live.update(self._layout)

    def print_request(self, text: str) -> None:
        self._layout['request_row'].visible = True
        self._layout['request'].visible = True
        self._request = text
        self._update_screen()

    def print_original_console(self, text: str) -> None:
        self._layout['request_row'].visible = True
        self._layout['original_console'].visible = True
        self._original_console = text
        self._update_screen()

    def print_log(self, text: str) -> None:
        self._log.append(text.strip())
        self._log = self._log[-100:]
        self._update_screen()

    def sleep(self, duration: float) -> None:
        self._layout['progress'].visible = True
        task = self._progress.add_task('sleeping', total=duration, remaining=int(duration))

        quantifier = 0.5
        elapsed = 0
        while elapsed < duration:
            remaining = max(0, duration - elapsed)
            self._progress.update(task, completed=elapsed, remaining=int(remaining))
            elapsed += quantifier
            time.sleep(quantifier)

        self._progress.update(task, completed=duration, remaining=0)
        self._progress.remove_task(task)

    def stop(self) -> None:
        self._live.stop()


class ConsoleToRich(TextIOBase):
    def __init__(self, rich_log: RichLog) -> None:
        self.rich_log = rich_log

    def write(self, s: str) -> int:
        to_send = s.strip()
        if to_send:
            self.rich_log.print_original_console(to_send)
        return len(to_send)

    def flush(self) -> None:
        pass


@dataclass
class _ShopRowState:
    request_text: str = ''
    timer_text: str = ''
    timer_visible: bool = False
    api_text: str = ''
    api_visible: bool = False


class RichLogMulti:
    SHOP_STYLES = ('green', 'yellow', 'blue', 'magenta', 'cyan', 'white', 'red')

    def __init__(self, header: str, shop_names: list[str], header_style='bold white on blue') -> None:
        self.header = header
        self.header_style = header_style
        self.request_style = 'bold bright_green'
        self.timer_style = 'bold blue'
        self.original_console_style = 'italic white'
        self.log_style = 'italic white'
        self._global_timer_text = Text()
        self._global_timer_visible = False
        self._shops = {shop_name: _ShopRowState() for shop_name in shop_names}
        self._shop_columns_count = self._get_shop_columns_count(len(shop_names))
        self._shop_rows_count = max(1, ceil(len(shop_names) / self._shop_columns_count))
        self._shop_styles = {
            shop_name: self.SHOP_STYLES[i % len(self.SHOP_STYLES)] for i, shop_name in enumerate(shop_names)
        }
        self._log = []
        self._lock = RLock()
        self._console = Console()
        self._layout = Layout()
        self._layout.split_column(
            Layout(name='header', size=3),
            Layout(name='shops', size=self._shop_rows_count * 3),
            Layout(name='global_timer', size=3, visible=False),
            Layout(name='log', ratio=1),
        )
        self._live = Live(self._layout, console=self._console, screen=True)
        self._update_screen()
        self._live.start()

    def _get_shop(self, shop_name: str) -> _ShopRowState:
        try:
            return self._shops[shop_name]
        except KeyError:
            raise KeyError(f'Unknown shop for RichLogMulti: {shop_name}') from None

    def _get_shop_columns_count(self, shops_count: int) -> int:
        if shops_count <= 4:
            return 1
        if shops_count <= 12:
            return 2
        return 3

    def _build_timer_text(self, duration: float, elapsed: float, style: str, bar_width: int = 24) -> Text:
        total = max(duration, 0.1)
        completed = min(elapsed / total, 1)
        remaining = max(0, int(duration - elapsed))
        filled = int(bar_width * completed)
        bar = '█' * filled + '░' * (bar_width - filled)
        return Text(f'До запроса: {remaining}с  {bar} {completed * 100:>3.0f}%', style=style)

    def _global_timer_bar_width(self) -> int:
        return max(24, self._console.size.width - 32)

    @property
    def visible_log(self) -> Text:
        console_width = self._console.size.width - 4
        log_height = self._layout['log'].size or (self._console.size.height - 3 - max(1, len(self._shops)) * 3)
        max_lines = max(0, log_height - 2)
        wrapped_lines = []
        for msg in self._log:
            line_style = self._get_log_style(msg)
            text_obj = Text(msg, style=line_style)
            wrapped_lines.extend(text_obj.wrap(self._console, console_width))
        visible_lines = wrapped_lines[-max_lines:]

        log_visible = Text()
        for line in visible_lines:
            log_visible.append_text(line)
            log_visible.append('\n')
        return log_visible

    def _get_log_style(self, msg: str) -> str | None:
        for shop_name, style in self._shop_styles.items():
            if shop_name in msg:
                return style
        return None

    def _render_shop_row(self, shop_name: str, shop: _ShopRowState) -> Table:
        grid = Table.grid(expand=True)
        shop_style = self._shop_styles[shop_name]
        grid.add_column(ratio=5)
        if shop.timer_visible:
            grid.add_column(ratio=2)
        if shop.api_visible:
            grid.add_column(ratio=2)

        panels = [
            Panel(
                Text(shop.request_text, style=shop_style),
                title=shop_name,
                height=3,
                border_style=shop_style,
            )
        ]

        if shop.timer_visible:
            panels.append(Panel(shop.timer_text, title='Timer', height=3, border_style=shop_style))

        if shop.api_visible:
            panels.append(
                Panel(
                    Text(shop.api_text, style=self.original_console_style),
                    title='API',
                    height=3,
                    border_style=shop_style,
                )
            )

        grid.add_row(*panels)
        return grid

    def _render_shop_rows(self) -> Table:
        rows_grid = Table.grid(expand=True)
        shop_items = list(self._shops.items())
        for row_index in range(0, len(shop_items), self._shop_columns_count):
            row_grid = Table.grid(expand=True)
            row_items = shop_items[row_index : row_index + self._shop_columns_count]
            for _ in range(self._shop_columns_count):
                row_grid.add_column(ratio=1)
            row_grid.add_row(
                *[
                    self._render_shop_row(shop_name, shop)
                    for shop_name, shop in row_items
                ],
                *['' for _ in range(self._shop_columns_count - len(row_items))],
            )
            rows_grid.add_row(row_grid)
        return rows_grid

    def _update_screen(self) -> None:
        self._layout['header'].update(
            Panel(Align(self.header, style=self.header_style, align='center'), height=3, expand=True)
        )
        self._layout['shops'].update(self._render_shop_rows())
        self._layout['global_timer'].visible = self._global_timer_visible
        self._layout['global_timer'].update(
            Panel(self._global_timer_text, title='Timer', height=3, border_style='blue')
        )
        self._layout['log'].update(Panel(self.visible_log, title='Log', expand=True, padding=(0, 1)))
        self._live.update(self._layout)

    def print_to_request_area(self, shop_name: str, text: str) -> None:
        with self._lock:
            self._get_shop(shop_name).request_text = text
            self._update_screen()

    def print_to_original_console_area(self, shop_name: str, text: str) -> None:
        with self._lock:
            shop = self._get_shop(shop_name)
            shop.api_text = text
            shop.api_visible = True
            self._update_screen()

    def console_to_rich_log_redirector(self, shop_name: str) -> 'ConsoleToRichMulti':
        self._get_shop(shop_name)
        return ConsoleToRichMulti(self, shop_name)

    def print_log(self, text: str) -> None:
        with self._lock:
            self._log.append(text.strip())
            self._log = self._log[-100:]
            self._update_screen()

    def sleep(self, duration: float | str, shop_name: str | float | None = None) -> None:
        if isinstance(duration, str):
            duration, shop_name = shop_name, duration
        if duration is None:
            raise ValueError('Sleep duration is required')

        if shop_name is not None:
            self._sleep_shop(shop_name=str(shop_name), duration=float(duration))
            return
        self._sleep_global(duration=float(duration))

    def _sleep_shop(self, shop_name: str, duration: float) -> None:
        quantifier = 0.5
        elapsed = 0
        while elapsed < duration:
            with self._lock:
                shop = self._get_shop(shop_name)
                shop.timer_visible = True
                shop.timer_text = self._build_timer_text(duration, elapsed, self._shop_styles[shop_name])
                self._update_screen()
            elapsed += quantifier
            time.sleep(quantifier)
        with self._lock:
            shop = self._get_shop(shop_name)
            shop.timer_text = self._build_timer_text(duration, duration, self._shop_styles[shop_name])
            self._update_screen()

    def _sleep_global(self, duration: float) -> None:
        quantifier = 0.5
        elapsed = 0
        while elapsed < duration:
            with self._lock:
                self._global_timer_visible = True
                self._global_timer_text = self._build_timer_text(
                    duration,
                    elapsed,
                    self.timer_style,
                    bar_width=self._global_timer_bar_width(),
                )
                self._update_screen()
            elapsed += quantifier
            time.sleep(quantifier)
        with self._lock:
            self._global_timer_text = self._build_timer_text(
                duration,
                duration,
                self.timer_style,
                bar_width=self._global_timer_bar_width(),
            )
            self._update_screen()

    def stop(self) -> None:
        self._live.stop()


class ConsoleToRichMulti(TextIOBase):
    def __init__(self, rich_log: RichLogMulti, shop_name: str) -> None:
        self.rich_log = rich_log
        self.shop_name = shop_name

    def write(self, s: str) -> int:
        to_send = s.strip()
        if to_send:
            self.rich_log.print_to_original_console_area(self.shop_name, to_send)
        return len(to_send)

    def flush(self) -> None:
        pass


if __name__ == '__main__':
    logger.remove()
    rich_log = RichLog(__file__)
    logger.add(lambda msg: rich_log.print_log(msg.strip()), level='DEBUG', colorize=True)
    try:
        for i in range(200):
            rich_log.print_request(f'Request {i}')
            with redirect_stdout(rich_log.console_to_rich_log_redirector):
                some_api_work(i)
            logger.debug(f'Line {i}')
            rich_log.sleep(2)
    finally:
        rich_log.stop()
