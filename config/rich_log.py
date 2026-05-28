import time
from dataclasses import dataclass
from math import ceil
from threading import RLock
from rich.align import Align
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


@dataclass
class _ShopRowState:
    request_text: str = ''
    timer_text: str = ''
    timer_visible: bool = False
    api_text: str = ''
    api_visible: bool = False


class RichLogMulti:
    SHOP_STYLES = (
        'green',
        'bright_yellow',
        'bright_cyan',
        'white',
        'bright_red',
        'bright_green',
        'yellow',
        'bright_magenta',
        'cyan',
        'red',
        'bright_blue',
    )

    def __init__(self, header: str, shop_names: list[str], header_style='bold white on blue') -> None:
        self.header = header
        self.header_style = header_style
        self.request_style = 'bold bright_green'
        self.timer_style = 'bold blue'
        self.original_console_style = 'italic white'
        self.log_style = 'italic white'
        self._global_text = ''
        self._global_visible = False
        self._global_timer_text = Text()
        self._global_timer_visible = False
        self._shops = {shop_name: _ShopRowState() for shop_name in shop_names}
        self._shop_columns_count = self._get_shop_columns_count(len(shop_names))
        self._shop_rows_count = max(1, ceil(len(shop_names) / self._shop_columns_count))
        self._shop_styles = {
            shop_name: self.SHOP_STYLES[i % len(self.SHOP_STYLES)] for i, shop_name in enumerate(shop_names)
        }
        self._log: list[tuple[str, str | None]] = []
        self._lock = RLock()
        self._console = Console()
        self._layout = Layout()
        self._layout.split_column(
            Layout(name='header', size=3),
            Layout(name='shops', size=self._shop_rows_count * 3),
            Layout(name='global_row', size=3, visible=False),
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
        if shops_count <= 8:
            return 2
        if shops_count < 16:
            return 3
        return 4

    def _build_timer_text(self, duration: float, elapsed: float, style: str, bar_width: int = 24) -> Text:
        total = max(duration, 0.1)
        completed = min(elapsed / total, 1)
        remaining = max(0, int(duration - elapsed))
        filled = int(bar_width * completed)
        bar = '█' * filled + '░' * (bar_width - filled)
        return Text(f'До запроса: {remaining}с  {bar} {completed * 100:>3.0f}%', style=style)

    def _global_timer_bar_width(self) -> int:
        timer_panel_width = self._console.size.width // 2
        return max(8, timer_panel_width - 24)

    @property
    def visible_log(self) -> Text:
        console_width = self._console.size.width - 6
        log_panel_height = self._get_log_panel_height()
        max_lines = max(0, log_panel_height - 2)
        visible_messages = self._log[-max_lines:]

        log_visible = Text()
        for msg, source_name in visible_messages:
            text_obj = Text.from_ansi(msg)
            style = self._shop_styles.get(source_name) if source_name is not None else None
            if style is not None:
                text_obj.stylize(style)
            text_obj.no_wrap = True
            text_obj.overflow = 'crop'
            text_obj.truncate(max_width=console_width, overflow='crop')
            log_visible.append_text(text_obj)
            log_visible.append('\n')
        return log_visible

    def _get_log_panel_height(self) -> int:
        used_height = 3 + self._shop_rows_count * 3
        if self._global_visible or self._global_timer_visible:
            used_height += 3
        return max(3, self._console.size.height - used_height)

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

        panels = [Panel(Text(shop.request_text, style=shop_style), title=shop_name, height=3, border_style=shop_style)]

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
                *[self._render_shop_row(shop_name, shop) for shop_name, shop in row_items],
                *['' for _ in range(self._shop_columns_count - len(row_items))],
            )
            rows_grid.add_row(row_grid)
        return rows_grid

    def _render_global_row(self) -> Table:
        row = Table.grid(expand=True)
        row.add_column(ratio=1)
        row.add_column(ratio=1)
        row.add_row(
            Panel(self._global_timer_text, title='Timer', height=3, border_style='blue'),
            Panel(Text(self._global_text, style=self.request_style), title='Global', height=3, border_style='green'),
        )
        return row

    def _update_screen(self) -> None:
        self._layout['header'].update(
            Panel(Align(self.header, style=self.header_style, align='center'), height=3, expand=True)
        )
        self._layout['shops'].update(self._render_shop_rows())
        self._layout['global_row'].visible = self._global_visible or self._global_timer_visible
        self._layout['global_row'].update(self._render_global_row())
        self._layout['log'].update(Panel(self.visible_log, title='Log', expand=True, padding=(0, 1)))
        self._live.update(self._layout)

    def print_to_area(self, text: str, area: str = 'request', shop_name: str | None = None) -> None:
        with self._lock:
            match area:
                case 'request':
                    if shop_name is None:
                        raise ValueError('shop_name is required for request area')
                    self._get_shop(shop_name).request_text = text
                case 'console':
                    if shop_name is None:
                        raise ValueError('shop_name is required for console area')
                    shop = self._get_shop(shop_name)
                    shop.api_text = text
                    shop.api_visible = True
                case 'global':
                    self._global_text = text
                    self._global_visible = True
                case _:
                    raise ValueError(f'Unknown rich log area: {area}')
            self._update_screen()

    def print_log(self, text: str, source_name: str | None = None) -> None:
        with self._lock:
            self._log.append((text.strip(), source_name))
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
                    duration, elapsed, self.timer_style, bar_width=self._global_timer_bar_width()
                )
                self._update_screen()
            elapsed += quantifier
            time.sleep(quantifier)
        with self._lock:
            self._global_timer_text = self._build_timer_text(
                duration, duration, self.timer_style, bar_width=self._global_timer_bar_width()
            )
            self._update_screen()

    def stop(self) -> None:
        self._live.stop()
