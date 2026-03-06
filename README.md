# Nova Language

<img width="500" height="500" alt="NovaLanguageIcon" src="https://github.com/user-attachments/assets/7e5721bc-2762-449a-94c0-0e8ce235603e" />

Nova is simple but powerful and a beginner-friendly coding language made by me (KerbalMissile). This specific project does include a basic GUI compiler that translates Nova code into C# then uses Window's default C# compiler to turn it into `.exe` or `.dll` files for use.

---

## Features

- **Natural Syntax:** Nova uses keywords like `have` (variables), `put` (print), `when` (if), and `otherwise` (else) to make code easier to read.
- **GUI Support:** Nova also can be used to make extremely basic UI's / Apps using basic code. A basic UI can be done in about 15 lines of code excluding spaces or comments.
- **GUI Compilation:** For compilation, Nova uses a GUI written in Python that can be used to compile it into .dll's or .exe's, it first converts to C# then uses Window's default C# compiler to convert it into .dll's or .exe's
- **Icon Handling:** Nova uses .ico for the icons displayed in UI's but will have automatic translation to .ico's from PNG's in the future.
- **Open Source:** Licensed under the GNU GPL v3 to keep Nova free and open for the community. It isn't too complex and I didn't think it needed to be more private, I would love as many suggestions or contributions as possible!

---

## Getting Started

### Prerequisites

- **Python 3.7+** required to run the Nova compiler GUI (`nova_compiler.py`).
- **.NET SDK or .NET Framework** required to compile the generated C# code. Make sure that `csc.exe` is installed and accessible.
- **Pillow** Python Library for the automatic PNG to ICO conversion, not working so it is a soft dependency for now. You can install it using `pip install pillow`


### Running The Compiler

- Place your `.nova` files in a folder.
- Run the compiler GUI (nova_compiler.py)
- Choose the folder containing your `.nova` files
- Select the target output type (`.exe` or `.dll`)
- Select one or more `.nova` files and click **Compile Selected**.
- The compiled files will appear in the same folder

---

## Writing Nova Code

Nova use a clean natural syntax, here's a `Hello, World!` example:

```put("Hello, World!")```

For more info and documentaion on how to code in Nova look at the [wiki](https://github.com/KerbalMissile/Nova-Coding-Language/wiki).

---

## Icon Handling

- You can specify an icon with `set_icon("path/to/icon.ico)`, png support will be added soon.
- PNG icons will be converted to ICO later on but for now it doesn't work and you need .ico's
- Make sure the icon path is correct relative to your `.nova` file.

---

## License

This project is licensed under the **GNU General Public License v3 (GPLv3)**

You are free to use, modify, and distribute Nova under the terms of the license, which requires any derivative works also remain open source uner GPLv3.

---

## Contributing

Contributions, bug reports, and feature requests are welcome! Feel free to pen issues, or submit pull requests on GitHub.

---

## Contact

For questions or help, open an issue on the GitHub repo or reach out to me, KerbalMissile. You can find me on Discord, username is **kerbalmissile**, aswell.

---


Thank you for looking at or maybe even trying out Nova!








