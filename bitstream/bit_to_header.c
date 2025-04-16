#include <stdio.h>
#include <stdlib.h>

void write_header(const char *input_file, const char *output_file, const char *array_name) {
    FILE *in = fopen(input_file, "rb");
    if (!in) {
        perror("Failed to open input file");
        exit(1);
    }

    FILE *out = fopen(output_file, "w");
    if (!out) {
        perror("Failed to open output file");
        fclose(in);
        exit(1);
    }

    fseek(in, 0, SEEK_END);
    size_t filesize = ftell(in);
    rewind(in);

    fprintf(out, "#ifndef %s_H\n", array_name);
    fprintf(out, "#define %s_H\n\n", array_name);
    fprintf(out, "#include <stdint.h>\n\n");
    fprintf(out, "#define %s_SIZE %zu\n", array_name, filesize);
    fprintf(out, "const uint8_t %s[%zu] = {\n", array_name, filesize);

    for (size_t i = 0; i < filesize; i++) {
        int byte = fgetc(in);
        if (byte == EOF) break;
        fprintf(out, "0x%02X", byte);
        if (i != filesize - 1) fprintf(out, ",");
        if ((i + 1) % 12 == 0) fprintf(out, "\n");
        else fprintf(out, " ");
    }

    fprintf(out, "\n};\n\n#endif\n");

    fclose(in);
    fclose(out);
    printf("Header written to %s with %zu bytes.\n", output_file, filesize);
}

int main(int argc, char *argv[]) {
    if (argc < 4) {
        printf("Usage: %s input.bit output.h array_name\n", argv[0]);
        return 1;
    }

    write_header(argv[1], argv[2], argv[3]);
    return 0;
}

