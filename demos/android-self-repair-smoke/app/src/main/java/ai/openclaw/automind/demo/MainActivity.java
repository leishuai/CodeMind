package ai.openclaw.automind.demo;

import android.app.Activity;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

public class MainActivity extends Activity {
    public static final String PACKAGE_NAME = "ai.openclaw.automind.demo";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setGravity(Gravity.CENTER_HORIZONTAL);
        root.setPadding(48, 96, 48, 48);

        TextView title = new TextView(this);
        title.setText("CodeMind Android Harness Demo");
        title.setTextSize(24);
        title.setGravity(Gravity.CENTER);
        title.setContentDescription("demo_title");
        root.addView(title, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
        ));

        TextView result = new TextView(this);
        result.setText("Probe state: Idle");
        result.setTextSize(18);
        result.setGravity(Gravity.CENTER);
        result.setContentDescription("probe_state_label");
        LinearLayout.LayoutParams resultParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
        );
        resultParams.setMargins(0, 64, 0, 32);
        root.addView(result, resultParams);

        Button button = new Button(this);
        button.setText("Run Probe");
        button.setContentDescription("probe_button");
        button.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                result.setText("Probe state: Completed");
            }
        });
        root.addView(button, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
        ));

        setContentView(root);
    }
}
