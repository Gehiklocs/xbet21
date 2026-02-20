# human_behavior.py
import asyncio
import random
import math
from typing import Tuple, List, Optional

from selenium.webdriver.support.wait import WebDriverWait


class HumanBehavior:
    """Simulates human-like behavior in browser automation"""

    # Human typing speed variations (characters per minute)
    TYPING_SPEEDS = {
        'slow': (150, 200),  # Slow typer
        'normal': (250, 350),  # Average typer
        'fast': (400, 500),  # Fast typer
        'hunt_peck': (100, 150)  # Hunt and peck typing
    }

    @staticmethod
    async def random_delay(min_seconds: float = 0.5, max_seconds: float = 2.0) -> None:
        """Random delay with human-like variation"""
        delay = random.uniform(min_seconds, max_seconds)
        # Add small random variations
        delay *= random.uniform(0.9, 1.1)
        await asyncio.sleep(delay)

    @staticmethod
    async def human_mouse_movement(page, target_x: int, target_y: int) -> None:
        """Move mouse like a human - with curves and varying speed"""
        try:
            # Get current mouse position
            current_pos = await page.evaluate("""() => {
                return {x: window.mouseX || 0, y: window.mouseY || 0};
            }""")

            start_x, start_y = current_pos['x'], current_pos['y']

            # Calculate distance
            distance = math.sqrt((target_x - start_x) ** 2 + (target_y - start_y) ** 2)

            # Generate bezier curve control points for natural movement
            if distance > 100:
                # Add curve for longer movements
                control1_x = start_x + (target_x - start_x) * 0.25 + random.randint(-50, 50)
                control1_y = start_y + (target_y - start_y) * 0.25 + random.randint(-50, 50)
                control2_x = start_x + (target_x - start_x) * 0.75 + random.randint(-50, 50)
                control2_y = start_y + (target_y - start_y) * 0.75 + random.randint(-50, 50)

                # Move in steps
                steps = max(20, int(distance / 10))
                for i in range(1, steps + 1):
                    t = i / steps
                    # Cubic bezier
                    x = (1 - t) ** 3 * start_x + 3 * (1 - t) ** 2 * t * control1_x + 3 * (
                                1 - t) * t ** 2 * control2_x + t ** 3 * target_x
                    y = (1 - t) ** 3 * start_y + 3 * (1 - t) ** 2 * t * control1_y + 3 * (
                                1 - t) * t ** 2 * control2_y + t ** 3 * target_y

                    # Add micro-jitter
                    x += random.uniform(-1, 1)
                    y += random.uniform(-1, 1)

                    await page.mouse.move(x, y)

                    # Variable speed - slower at start and end
                    speed_factor = math.sin(t * math.pi)  # Bell curve
                    delay = 0.005 + (0.01 * (1 - speed_factor))
                    await asyncio.sleep(delay)
            else:
                # Straight movement for short distances
                steps = max(5, int(distance / 5))
                for i in range(1, steps + 1):
                    t = i / steps
                    x = start_x + (target_x - start_x) * t
                    y = start_y + (target_y - start_y) * t

                    # Add micro-jitter
                    x += random.uniform(-0.5, 0.5)
                    y += random.uniform(-0.5, 0.5)

                    await page.mouse.move(x, y)
                    await asyncio.sleep(random.uniform(0.008, 0.015))

            # Store final position
            await page.evaluate(f"window.mouseX = {target_x}; window.mouseY = {target_y};")

        except Exception as e:
            # Fallback to direct move
            await page.mouse.move(target_x, target_y)

    @staticmethod
    async def human_click(page, selector: str = None, x: int = None, y: int = None) -> None:
        """Click like a human - move, hover, pause, click"""
        try:
            if selector:
                # Get element position
                bbox = await page.locator(selector).bounding_box()
                if not bbox:
                    raise Exception(f"Element not found: {selector}")

                # Click slightly off-center (human-like)
                offset_x = random.uniform(2, bbox['width'] - 2)
                offset_y = random.uniform(2, bbox['height'] - 2)
                target_x = bbox['x'] + offset_x
                target_y = bbox['y'] + offset_y
            else:
                target_x, target_y = x, y

            # Move mouse naturally
            await HumanBehavior.human_mouse_movement(page, target_x, target_y)

            # Small pause before click
            await HumanBehavior.random_delay(0.1, 0.3)

            # Click with human-like pressure variation
            await page.mouse.down()
            await asyncio.sleep(random.uniform(0.05, 0.15))  # Hold duration
            await page.mouse.up()

            # Post-click pause
            await HumanBehavior.random_delay(0.2, 0.5)

        except Exception as e:
            print(f"⚠️ Human click error: {e}")
            # Fallback to normal click
            if selector:
                await page.click(selector)
            else:
                await page.mouse.click(x, y)

    @staticmethod
    async def human_type(page, selector: str, text: str, typing_speed: str = 'normal') -> None:
        """Type text like a human with variable speed and occasional mistakes"""
        try:
            # Click the input first
            await HumanBehavior.human_click(page, selector)

            # Clear field (sometimes humans press Ctrl+A, sometimes they don't)
            if random.random() > 0.3:
                # Select all
                await page.keyboard.press('Control+A')
                await HumanBehavior.random_delay(0.1, 0.2)
                await page.keyboard.press('Backspace')
            else:
                # Clear manually
                current_value = await page.input_value(selector)
                for _ in range(len(current_value)):
                    await page.keyboard.press('Backspace')
                    await asyncio.sleep(random.uniform(0.05, 0.12))

            await HumanBehavior.random_delay(0.3, 0.7)

            # Get typing speed range
            speed_range = HumanBehavior.TYPING_SPEEDS.get(typing_speed, (250, 350))
            chars_per_minute = random.randint(*speed_range)
            delay_between_chars = 60.0 / chars_per_minute

            # Occasionally make and correct mistakes (5% chance)
            mistake_probability = 0.05

            for i, char in enumerate(text):
                # Decide whether to make a mistake
                make_mistake = random.random() < mistake_probability and len(text) > 5

                if make_mistake:
                    # Type wrong character
                    wrong_char = random.choice('abcdefghijklmnopqrstuvwxyz')
                    await page.keyboard.type(wrong_char)
                    await asyncio.sleep(delay_between_chars * random.uniform(1.5, 2.5))

                    # Realize mistake and correct
                    await page.keyboard.press('Backspace')
                    await asyncio.sleep(delay_between_chars * random.uniform(0.8, 1.5))

                    # Type correct character
                    await page.keyboard.type(char)
                else:
                    # Type correct character
                    await page.keyboard.type(char)

                # Variable delay between keystrokes
                base_delay = delay_between_chars * random.uniform(0.7, 1.3)

                # Occasionally pause longer (like thinking)
                if random.random() < 0.02:  # 2% chance
                    base_delay *= random.uniform(3, 5)

                await asyncio.sleep(base_delay)

            # Pause after typing
            await HumanBehavior.random_delay(0.5, 1.0)

        except Exception as e:
            print(f"⚠️ Human type error: {e}")
            # Fallback to normal typing
            await page.fill(selector, text)

    @staticmethod
    async def human_scroll(page, direction: str = 'down', amount: Optional[int] = None) -> None:
        """Scroll like a human - variable speed and occasional pauses"""
        try:
            viewport_size = await page.evaluate("() => ({width: window.innerWidth, height: window.innerHeight})")
            scroll_height = await page.evaluate("() => document.documentElement.scrollHeight")

            current_scroll = await page.evaluate("() => window.scrollY")

            if amount is None:
                # Scroll random amount
                if direction == 'down':
                    amount = random.randint(100, min(500, scroll_height - current_scroll))
                else:
                    amount = random.randint(100, min(500, current_scroll))

            target_scroll = current_scroll + amount if direction == 'down' else current_scroll - amount
            target_scroll = max(0, min(target_scroll, scroll_height - viewport_size['height']))

            # Scroll in steps with variable speed
            distance = abs(target_scroll - current_scroll)
            steps = max(5, int(distance / 30))

            for i in range(1, steps + 1):
                t = i / steps
                # Easing function for smooth scroll
                ease_t = 1 - (1 - t) ** 3  # Cubic ease-out

                scroll_pos = current_scroll + (target_scroll - current_scroll) * ease_t
                await page.evaluate(f"window.scrollTo({{top: {scroll_pos}, behavior: 'instant'}})")

                # Variable speed
                delay = 0.01 + (0.02 * math.sin(t * math.pi))
                await asyncio.sleep(delay)

            # Occasional pause mid-scroll
            if random.random() < 0.3:
                await HumanBehavior.random_delay(1, 2)

        except Exception as e:
            print(f"⚠️ Human scroll error: {e}")
            # Fallback
            if direction == 'down':
                await page.evaluate(f"window.scrollBy(0, {amount or 300})")
            else:
                await page.evaluate(f"window.scrollBy(0, -{amount or 300})")

    @staticmethod
    async def human_wait_for_page_load(page, min_time: float = 1.0, max_time: float = 3.0) -> None:
        """Wait for page load with human-like perception"""
        # Initial load perception
        await HumanBehavior.random_delay(min_time, max_time)

        # Check if page is still loading
        try:
            await page.wait_for_load_state('networkidle', timeout=5000)
        except:
            pass

        # Additional random wait (humans don't interact instantly)
        await HumanBehavior.random_delay(0.5, 1.5)

    @staticmethod
    async def human_look_at(page, selector: str = None, duration: float = None) -> None:
        """Simulate human looking at an element (hover with occasional micro-movements)"""
        try:
            if selector:
                bbox = await page.locator(selector).bounding_box()
                if bbox:
                    # Move to element
                    center_x = bbox['x'] + bbox['width'] / 2
                    center_y = bbox['y'] + bbox['height'] / 2
                    await HumanBehavior.human_mouse_movement(page, center_x, center_y)

                    # Look at it for a moment
                    look_time = duration or random.uniform(0.8, 2.5)

                    # Micro-movements while looking
                    end_time = asyncio.get_event_loop().time() + look_time
                    while asyncio.get_event_loop().time() < end_time:
                        # Small random movements
                        jitter_x = center_x + random.uniform(-5, 5)
                        jitter_y = center_y + random.uniform(-5, 5)
                        await page.mouse.move(jitter_x, jitter_y)
                        await asyncio.sleep(random.uniform(0.1, 0.3))

        except Exception as e:
            print(f"⚠️ Human look error: {e}")

    @staticmethod
    async def human_wait_for_page_load_selenium(driver, min_time: float = 1.0, max_time: float = 3.0):
        """Wait for page load with human-like perception for Selenium"""
        await HumanBehavior.random_delay(min_time, max_time)

        try:
            # Wait for document ready state
            WebDriverWait(driver, 10).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
        except:
            pass

        await HumanBehavior.random_delay(0.5, 1.5)